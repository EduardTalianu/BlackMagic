#!/usr/bin/env python3
"""
app.py - Main Flask application with hierarchical task management
"""
import os
import re
import docker
import requests
import traceback
import sys
from flask import Flask, request, render_template_string, jsonify, session
from flask_session import Session
import datetime
import json

# Import task management system
try:
    from src.task_translator import create_translator, TaskModel
    from src.task_manager import TaskManager
    print("✓ Task management modules imported successfully")
except ImportError as e:
    print(f"✗ Failed to import task management modules: {e}")
    traceback.print_exc()
    sys.exit(1)

app = Flask(__name__)
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

LLM_URL = os.getenv("LLM_BASE_URL", "https://api.moonshot.ai/v1") + "/chat/completions"
LLM_KEY = os.getenv("MOONSHOT_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "moonshot-v1-8k")
KALI_NAME = "kali-llm-web-kali-1"
WORK_DIR = "/app/work"
TASK_DIR = "/app/work"
LOG_DIR = "/app/logs"
SHARED_DIR = "/shared"

# Auto-continue configuration
AUTO_CONTINUE_DELAY = int(os.getenv("AUTO_CONTINUE_DELAY", "10"))

# Ensure directories exist
os.makedirs(TASK_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(SHARED_DIR, exist_ok=True)

INSTALL_LOG = os.path.join(LOG_DIR, "install.log")
ACTION_LOG = os.path.join(LOG_DIR, "actions.log")
LLM_RESPONSE_LOG = os.path.join(LOG_DIR, "llm_response.log")
TRANSLATION_LOG = os.path.join(LOG_DIR, "translation.log")

# Initialize task translator
try:
    task_translator = create_translator()
    print("✓ Task translator initialized successfully")
except Exception as e:
    print(f"✗ Warning: Task translator initialization failed: {e}")
    traceback.print_exc()
    task_translator = None

# Initialize global task manager
try:
    task_manager = TaskManager(
        container_name=KALI_NAME,
        llm_url=LLM_URL,
        llm_key=LLM_KEY,
        model=LLM_MODEL,
        work_dir=TASK_DIR
    )
    print("✓ Task manager initialized successfully")
except Exception as e:
    print(f"✗ Warning: Task manager initialization failed: {e}")
    traceback.print_exc()
    task_manager = None


def log_action(action, output):
    """Log all actions and their outputs to a file"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] Command: {action}\nOutput:\n{output}\n{'='*50}\n"
    with open(ACTION_LOG, 'a') as f:
        f.write(log_entry)


def log_llm_response(response):
    """Log LLM responses to a file"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] LLM Response:\n{response}\n{'='*50}\n"
    with open(LLM_RESPONSE_LOG, 'a') as f:
        f.write(log_entry)


def log_translation(user_request, translated_task):
    """Log task translations to a file"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] Translation:\nUser Request: {user_request}\nTranslated Task:\n{json.dumps(translated_task, indent=2)}\n{'='*50}\n"
    with open(TRANSLATION_LOG, 'a') as f:
        f.write(log_entry)


def get_install_log():
    """Read the installation log file"""
    if os.path.exists(INSTALL_LOG):
        with open(INSTALL_LOG, 'r') as f:
            return f.read()
    return "No tools installed yet."


def update_install_log(tool_name):
    """Add a tool to the installation log"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] Installed: {tool_name}\n"
    with open(INSTALL_LOG, 'a') as f:
        f.write(log_entry)


def get_system_prompt(task: TaskModel):
    """Generate system prompt with task context and installation history"""
    history = get_install_log()
    return f"""You are an expert penetration tester and security analyst working inside a Kali Linux Docker container.
You have full access to security tools and can install additional tools as needed.

TASK CONTEXT:
Abstract: {task.abstract}

Detailed Description:
{task.description}

Verification Criteria:
{task.verification}

CRITICAL OUTPUT FORMAT:
- Your response must be ONLY a bash command or 'DONE: summary'
- NO explanations, NO markdown formatting, NO code blocks
- Just the raw command to execute
- Example GOOD responses: 'nmap -p- 192.168.1.1' or 'DONE: Scan completed'
- Example BAD responses: 'Let me scan the host: nmap ...' or '```bash\\nnmap ...\\n```'

CRITICAL INSTRUCTIONS:
- Execute ONLY ONE command at a time
- WAIT for the output of each command before proceeding
- After seeing the output, decide what to do next
- Do not provide multiple commands at once
- Do not create multi-step plans in advance
- Focus on the immediate next step only
- Work towards completing the task as described above

Your environment:
- You are running inside a Kali Linux Docker container
- You can execute commands one by one and wait for output before proceeding
- You can write files to /app/work directory
- You can view files using commands like 'cat', 'less', 'more', etc.
- You can append content to files using '>>' redirection
- All files should be saved in /app/work directory

Your approach:
- Follow the task description as your guide
- Decide on only the FIRST step to take
- Execute one command for that step
- Wait for the output
- Based on the output, decide the next step
- Continue until all verification criteria are met

Guidelines:
- Always use /app/work for storing files (create it if needed)
- When you need to install a tool, use: apt update && apt install -y toolname
- Issue ONLY ONE command at a time
- You can create multiple files (reports, scripts, notes) in /app/work
- To view files, use commands like 'cat filename', 'less filename', etc.
- To append content to files, use 'echo "content" >> filename'
- When all verification criteria are met, respond with 'DONE: [summary]'
- In your DONE summary, reference the verification criteria and confirm they are met

Installation history:
{history}

REMEMBER: Respond with ONLY the command, nothing else. No explanations, no markdown, just the command."""


def extract_command(response: str) -> str:
    """Extract the actual command from LLM response, removing markdown and explanations"""
    response = response.strip()
    
    if response.startswith("DONE:"):
        return response
    
    # Remove markdown code blocks
    code_block_pattern = r'```(?:bash|sh)?\s*\n(.*?)\n```'
    matches = re.findall(code_block_pattern, response, re.DOTALL)
    if matches:
        return matches[0].strip()
    
    # Remove any leading explanatory text
    lines = response.split('\n')
    command_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if any(phrase in line.lower() for phrase in [
            'let me', 'i will', 'i need to', 'i\'ll', 'first,', 'next,', 
            'now,', 'i apologize', 'i see', 'i notice', 'sorry'
        ]):
            continue
        command_lines.append(line)
    
    if command_lines:
        return command_lines[0]
    
    return response


def llm_next_command(conversation_history: list) -> str:
    payload = {
        "model": LLM_MODEL,
        "temperature": 0,
        "messages": conversation_history
    }
    hdr = {"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"}
    r = requests.post(LLM_URL, headers=hdr, json=payload, timeout=90)
    r.raise_for_status()
    response = r.json()["choices"][0]["message"]["content"].strip()
    
    log_llm_response(f"RAW: {response}")
    command = extract_command(response)
    log_llm_response(f"EXTRACTED: {command}")
    
    return command


def kali_exec(cmd: str) -> tuple[str, bool]:
    """Execute command and return (output, tool_installed)"""
    c = docker.from_env().containers.get(KALI_NAME)
    
    raw = c.exec_run(["/bin/bash", "-c", cmd], tty=False, stderr=True, stdout=True)
    output = raw.output.decode(errors="ignore")
    tool_installed = False
    
    # Check for "command not found" and auto-install
    if "command not found" in output:
        match = re.search(r"bash:.*?:\s*(\w+):\s*command not found", output)
        if match:
            missing_tool = match.group(1)
            install_cmd = f"apt-get update && apt-get install -y {missing_tool}"
            c.exec_run(["/bin/bash", "-c", install_cmd], tty=False, stderr=True, stdout=True)
            
            update_install_log(missing_tool)
            tool_installed = True
            
            raw = c.exec_run(["/bin/bash", "-c", cmd], tty=False, stderr=True, stdout=True)
            output = f"[System] Tool '{missing_tool}' was not found. Automatically installed it.\n\n" + raw.output.decode(errors="ignore")
    
    log_action(cmd, output)
    return output, tool_installed


def list_directory(path):
    """List the contents of a directory"""
    c = docker.from_env().containers.get(KALI_NAME)
    
    check_cmd = f"test -d {path} && echo 'DIR' || echo 'NOT_DIR'"
    raw = c.exec_run(["/bin/bash", "-c", check_cmd], tty=False, stderr=True, stdout=True)
    result = raw.output.decode(errors="ignore").strip()
    
    if result != "DIR":
        return []
    
    raw = c.exec_run(["/bin/bash", "-c", f"cd {path} && ls -la"], tty=False, stderr=True, stdout=True)
    output = raw.output.decode(errors="ignore")
    
    lines = output.strip().split('\n')[1:]
    files = []
    
    for line in lines:
        if not line.strip():
            continue
            
        parts = line.split()
        if len(parts) < 9:
            continue
            
        file_type = parts[0][0]
        permissions = parts[0]
        size = parts[4]
        name = ' '.join(parts[8:])
        
        files.append({
            'name': name,
            'type': 'directory' if file_type == 'd' else 'file',
            'permissions': permissions,
            'size': size,
            'full_path': os.path.join(path, name).replace('\\', '/')
        })
    
    return files


def get_file_content(path):
    """Get the content of a file"""
    c = docker.from_env().containers.get(KALI_NAME)
    
    check_cmd = f"test -f {path} && echo 'FILE' || echo 'NOT_FILE'"
    raw = c.exec_run(["/bin/bash", "-c", check_cmd], tty=False, stderr=True, stdout=True)
    result = raw.output.decode(errors="ignore").strip()
    
    if result != "FILE":
        return None, "Not a file"
    
    raw = c.exec_run(["/bin/bash", "-c", f"cat {path}"], tty=False, stderr=True, stdout=True)
    content = raw.output.decode(errors="ignore")
    
    return content, None


@app.route("/")
def index():
    with open("templates/index.html") as f:
        return render_template_string(f.read())


@app.route("/translate", methods=["POST"])
def translate():
    """Translate user request into structured task"""
    try:
        data = request.json
        user_request = data.get("request", "").strip()
        
        if not user_request:
            return jsonify({"error": "Request cannot be empty"}), 400
        
        if not task_translator:
            return jsonify({"error": "Task translator not initialized"}), 500
        
        # Translate the task
        translated_task = task_translator.translate_task(user_request)
        
        # Convert to dict for JSON response
        task_dict = translated_task.model_dump()
        
        # Log the translation
        log_translation(user_request, task_dict)
        
        return jsonify({
            "translated_task": task_dict,
            "original_request": user_request
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/task", methods=["POST"])
def create_task():
    """Create a new hierarchical task - returns immediately with task_id"""
    try:
        data = request.json
        translated_task = data.get("translated_task")
        
        if not translated_task:
            return jsonify({"error": "Translated task is required"}), 400
        
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        # Convert dict to TaskModel
        try:
            task = TaskModel(**translated_task)
        except Exception as e:
            return jsonify({"error": f"Invalid task structure: {e}"}), 400
        
        # Create task and get task_id
        task_id = task_manager.create_task(task)
        
        return jsonify({
            "task_id": task_id,
            "status": "pending",
            "message": "Task created and queued for execution"
        }), 202
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/task/status", methods=["GET"])
def get_all_tasks():
    """Get status of all tasks"""
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        tasks = task_manager.list_all_tasks()
        
        # Debug logging
        print(f"[API] Returning {len(tasks)} task/node entries")
        for t in tasks[:5]:  # Log first 5
            print(f"[API]   - {t.get('type', 'unknown')}: {t.get('abstract', 'N/A')[:50]}")
        
        return jsonify({"tasks": tasks})
        
    except Exception as e:
        print(f"[API] Error in get_all_tasks: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/task/<task_id>", methods=["GET"])
def get_task_status(task_id):
    """Get status of a specific task"""
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        status = task_manager.get_task_status(task_id)
        if not status:
            return jsonify({"error": "Task not found"}), 404
        
        # Log to console for debugging
        print(f"[API] Task {task_id} status: {status.get('status')}")
        if status.get('error'):
            print(f"[API] Task {task_id} error: {status.get('error')}")
        
        return jsonify(status)
        
    except Exception as e:
        print(f"[API] Error getting task status: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/task/<task_id>/stop", methods=["PUT"])
def cancel_task(task_id):
    """Cancel a running task"""
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        success = task_manager.cancel_task(task_id)
        if success:
            return jsonify({"status": "cancelled", "task_id": task_id})
        else:
            return jsonify({"error": "Task not found or cannot be cancelled"}), 400
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/tree", methods=["GET"])
def get_task_tree():
    """Get Mermaid graph for a task"""
    try:
        task_id = request.args.get('task_id')
        if not task_id:
            return jsonify({"error": "task_id parameter required"}), 400
        
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        graph = task_manager.get_task_graph(task_id)
        if not graph:
            return jsonify({"error": "Task not found or graph not available"}), 404
        
        return jsonify({"task_id": task_id, "graph": graph})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Legacy endpoints for backward compatibility
@app.route("/run", methods=["POST"])
def run():
    """Legacy endpoint - kept for backward compatibility"""
    try:
        data = request.json
        ask = data.get("ask", "").strip()
        is_continue = data.get("continue", False)
        translated_task = data.get("translated_task")
        
        # Initialize or get the conversation history
        if "conversation" not in session or not is_continue:
            # Validate that we have a translated task
            if not translated_task:
                return jsonify({"error": "Translated task is required for new conversations"}), 400
            
            # Convert dict to TaskModel
            try:
                task = TaskModel(**translated_task)
            except Exception as e:
                return jsonify({"error": f"Invalid task structure: {e}"}), 400
            
            # Start new conversation with task context
            session["conversation"] = [
                {"role": "system", "content": get_system_prompt(task)},
                {"role": "user", "content": f"Begin working on this task: {task.abstract}"}
            ]
            session["current_task"] = translated_task
            
            # Get the first command from LLM
            cmd = llm_next_command(session["conversation"])
            session["conversation"].append({"role": "assistant", "content": cmd})
            session["last_cmd"] = cmd
        else:
            # Continue with existing conversation
            cmd = session.get("last_cmd", "")
            if not cmd:
                return jsonify({"error": "No command to continue"}), 400
        
        # Execute the command
        output, tool_installed = kali_exec(cmd)
        
        # Add the command output to the conversation
        session["conversation"].append(
            {"role": "user", "content": f"Command output:\n{output}"}
        )
        
        # Get the next command from LLM
        next_action = llm_next_command(session["conversation"])
        session["conversation"].append({"role": "assistant", "content": next_action})
        session["last_cmd"] = next_action
        
        # Check if the LLM is done
        is_done = next_action.startswith("DONE:")
        
        # Prepare response
        response = {
            "output": f"$ {cmd}\n{output}",
            "llm_response": next_action,
            "done": is_done,
            "summary": next_action[5:].strip() if is_done else None,
            "auto_continue": not is_done,
            "auto_continue_delay": AUTO_CONTINUE_DELAY,
            "current_task": session.get("current_task")
        }
        
        # Clear conversation if done
        if is_done:
            session.pop("conversation", None)
            session.pop("last_cmd", None)
            session.pop("current_task", None)
        
        return jsonify(response)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/reset", methods=["POST"])
def reset():
    session.pop("conversation", None)
    session.pop("last_cmd", None)
    session.pop("current_task", None)
    return jsonify({"status": "Conversation reset"})


@app.route("/config", methods=["GET", "POST"])
def config():
    """Get or update configuration"""
    global AUTO_CONTINUE_DELAY
    
    if request.method == "GET":
        return jsonify({
            "auto_continue_delay": AUTO_CONTINUE_DELAY
        })
    else:
        data = request.json
        if "auto_continue_delay" in data:
            AUTO_CONTINUE_DELAY = int(data["auto_continue_delay"])
        return jsonify({
            "status": "Configuration updated",
            "auto_continue_delay": AUTO_CONTINUE_DELAY
        })


@app.route("/files", methods=["GET"])
def get_files():
    try:
        path = request.args.get('path', '/app')
        files = list_directory(path)
        parent = os.path.dirname(path) if path != '/' else None
        
        return jsonify({
            "files": files,
            "current_path": path,
            "parent_path": parent
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/file", methods=["GET"])
def get_file():
    try:
        path = request.args.get('path')
        if not path:
            return jsonify({"error": "Path parameter required"}), 400
        
        content, error = get_file_content(path)
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"content": content})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/shared_info", methods=["GET"])
def get_shared_info():
    """Get information about shared directories"""
    try:
        shared_info = {
            "shared_directory": "/shared",
            "work_directory": "/app/work",
            "logs_directory": "/app/logs",
            "host_shared_directory": "./shared",
            "host_work_directory": "./shared/work",
            "host_logs_directory": "./shared/logs"
        }
        return jsonify(shared_info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)