#!/usr/bin/env python3
import os
import re
import docker
import requests
from flask import Flask, request, render_template_string, jsonify, session, send_file
from flask_session import Session
import datetime
import json
import base64
import threading
import time
import shutil

app = Flask(__name__)
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

LLM_URL = os.getenv("LLM_BASE_URL", "https://api.moonshot.ai/v1") + "/chat/completions"
LLM_KEY = os.getenv("MOONSHOT_API_KEY")
KALI_NAME = "kali-llm-web-kali-1"
WORK_DIR = "/app"  # Main working directory
TASK_DIR = "/app/work"  # Directory for task-specific files (shared with host)
LOG_DIR = "/app/logs"  # Dedicated directory for logs (shared with host)
SHARED_DIR = "/shared"  # Additional shared directory

# Auto-continue configuration
AUTO_CONTINUE_DELAY = int(os.getenv("AUTO_CONTINUE_DELAY", "10"))  # Default 10 seconds

# Ensure directories exist
os.makedirs(TASK_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(SHARED_DIR, exist_ok=True)

INSTALL_LOG = os.path.join(LOG_DIR, "install.log")
ACTION_LOG = os.path.join(LOG_DIR, "actions.log")
LLM_RESPONSE_LOG = os.path.join(LOG_DIR, "llm_response.log")

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

def get_system_prompt():
    """Generate system prompt with installation history"""
    history = get_install_log()
    return (
        "You are an expert penetration tester and security analyst working inside a Kali Linux Docker container. "
        "You have full access to security tools and can install additional tools as needed.\n\n"
        "CRITICAL OUTPUT FORMAT:\n"
        "- Your response must be ONLY a bash command or 'DONE: summary'\n"
        "- NO explanations, NO markdown formatting, NO code blocks\n"
        "- Just the raw command to execute\n"
        "- Example GOOD responses: 'nmap -p- 192.168.1.1' or 'DONE: Scan completed'\n"
        "- Example BAD responses: 'Let me scan the host: nmap ...' or '```bash\\nnmap ...\\n```'\n\n"
        "CRITICAL INSTRUCTIONS:\n"
        "- Execute ONLY ONE command at a time\n"
        "- WAIT for the output of each command before proceeding\n"
        "- After seeing the output, decide what to do next\n"
        "- Do not provide multiple commands at once\n"
        "- Do not create multi-step plans in advance\n"
        "- Focus on the immediate next step only\n\n"
        "Your environment:\n"
        "- You are running inside a Kali Linux Docker container\n"
        "- You can execute commands one by one and wait for output before proceeding\n"
        "- You can write files to /app/work directory\n"
        "- You can view files using commands like 'cat', 'less', 'more', etc.\n"
        "- You can append content to files using '>>' redirection\n"
        "- All files should be saved in /app/work directory\n\n"
        "Your approach:\n"
        "- Analyze the user's request thoroughly\n"
        "- Decide on only the FIRST step to take\n"
        "- Execute one command for that step\n"
        "- Wait for the output\n"
        "- Based on the output, decide the next step\n"
        "- Continue this process until the task is complete\n\n"
        "Guidelines:\n"
        "- Always use /app/work for storing files (create it if needed)\n"
        "- When you need to install a tool, use: apt update && apt install -y toolname\n"
        "- Issue ONLY ONE command at a time\n"
        "- You can create multiple files (reports, scripts, notes) in /app/work\n"
        "- To view files, use commands like 'cat filename', 'less filename', etc.\n"
        "- To append content to files, use 'echo \"content\" >> filename'\n"
        "- When you believe the task is complete, respond with 'DONE: [summary]'\n\n"
        f"Installation history:\n{history}\n\n"
        "REMEMBER: Respond with ONLY the command, nothing else. No explanations, no markdown, just the command."
    )

def extract_command(response: str) -> str:
    """Extract the actual command from LLM response, removing markdown and explanations"""
    response = response.strip()
    
    # Check if it's a DONE status
    if response.startswith("DONE:"):
        return response
    
    # Remove markdown code blocks
    # Pattern: ```bash\ncommand\n``` or ```\ncommand\n```
    code_block_pattern = r'```(?:bash|sh)?\s*\n(.*?)\n```'
    matches = re.findall(code_block_pattern, response, re.DOTALL)
    if matches:
        # Return the first code block content
        return matches[0].strip()
    
    # Remove any leading explanatory text (lines that don't look like commands)
    lines = response.split('\n')
    command_lines = []
    for line in lines:
        line = line.strip()
        # Skip empty lines and common explanatory phrases
        if not line:
            continue
        if any(phrase in line.lower() for phrase in [
            'let me', 'i will', 'i need to', 'i\'ll', 'first,', 'next,', 
            'now,', 'i apologize', 'i see', 'i notice', 'sorry'
        ]):
            continue
        # This looks like a command line
        command_lines.append(line)
    
    if command_lines:
        return command_lines[0]
    
    # If all else fails, return the original response
    return response

def llm_next_command(conversation_history: list) -> str:
    payload = {
        "model": os.getenv("LLM_MODEL", "moonshot-v1-8k"),
        "temperature": 0,
        "messages": conversation_history
    }
    hdr = {"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"}
    r = requests.post(LLM_URL, headers=hdr, json=payload, timeout=90)
    r.raise_for_status()
    response = r.json()["choices"][0]["message"]["content"].strip()
    
    # Log the raw LLM response
    log_llm_response(f"RAW: {response}")
    
    # Extract the actual command
    command = extract_command(response)
    
    # Log the extracted command
    log_llm_response(f"EXTRACTED: {command}")
    
    return command

def kali_exec(cmd: str) -> tuple[str, bool]:
    """Execute command and return (output, tool_installed)"""
    c = docker.from_env().containers.get(KALI_NAME)
    
    # Execute the command
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
            
            # Update install log
            update_install_log(missing_tool)
            tool_installed = True
            
            # Retry the original command after installation
            raw = c.exec_run(["/bin/bash", "-c", cmd], tty=False, stderr=True, stdout=True)
            output = f"[System] Tool '{missing_tool}' was not found. Automatically installed it.\n\n" + raw.output.decode(errors="ignore")
    
    # Log the action
    log_action(cmd, output)
    
    return output, tool_installed

def list_directory(path):
    """List the contents of a directory"""
    c = docker.from_env().containers.get(KALI_NAME)
    
    # Ensure path exists
    check_cmd = f"test -d {path} && echo 'DIR' || echo 'NOT_DIR'"
    raw = c.exec_run(["/bin/bash", "-c", check_cmd], tty=False, stderr=True, stdout=True)
    result = raw.output.decode(errors="ignore").strip()
    
    if result != "DIR":
        return []
    
    # List directory contents with details
    raw = c.exec_run(["/bin/bash", "-c", f"cd {path} && ls -la"], tty=False, stderr=True, stdout=True)
    output = raw.output.decode(errors="ignore")
    
    # Parse the output to get file/directory list
    lines = output.strip().split('\n')[1:]  # Skip the first line (total)
    files = []
    
    for line in lines:
        if not line.strip():
            continue
            
        parts = line.split()
        if len(parts) < 9:
            continue
            
        file_type = parts[0][0]  # First character indicates file type
        permissions = parts[0]
        size = parts[4]
        name = ' '.join(parts[8:])  # Handle filenames with spaces
        
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
    
    # Check if it's a file
    check_cmd = f"test -f {path} && echo 'FILE' || echo 'NOT_FILE'"
    raw = c.exec_run(["/bin/bash", "-c", check_cmd], tty=False, stderr=True, stdout=True)
    result = raw.output.decode(errors="ignore").strip()
    
    if result != "FILE":
        return None, "Not a file"
    
    # Get file content
    raw = c.exec_run(["/bin/bash", "-c", f"cat {path}"], tty=False, stderr=True, stdout=True)
    content = raw.output.decode(errors="ignore")
    
    return content, None

@app.route("/")
def index():
    with open("templates/index.html") as f:
        return render_template_string(f.read())

@app.route("/run", methods=["POST"])
def run():
    try:
        data = request.json
        ask = data.get("ask", "").strip()
        is_continue = data.get("continue", False)
        
        # Initialize or get the conversation history
        if "conversation" not in session or not is_continue:
            # Start new conversation
            session["conversation"] = [
                {"role": "system", "content": get_system_prompt()},
                {"role": "user", "content": ask}
            ]
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
            "auto_continue": not is_done,  # Auto-continue unless done
            "auto_continue_delay": AUTO_CONTINUE_DELAY
        }
        
        # Clear conversation if done
        if is_done:
            session.pop("conversation", None)
            session.pop("last_cmd", None)
        
        return jsonify(response)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/reset", methods=["POST"])
def reset():
    session.pop("conversation", None)
    session.pop("last_cmd", None)
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
        
        # Get parent directory for navigation
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
