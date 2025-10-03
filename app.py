#!/usr/bin/env python3
"""
app.py - Main Flask application with hierarchical task management
"""
import os
import docker
import traceback
import sys
from flask import Flask, request, render_template_string, jsonify
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

LLM_URL = os.getenv("LLM_BASE_URL", "https://api.moonshot.ai/v1") + "/chat/completions"
LLM_KEY = os.getenv("MOONSHOT_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "moonshot-v1-8k")
KALI_NAME = "kali-llm-web-kali-1"
WORK_DIR = "/app/work"
TASK_DIR = "/app/work"
LOG_DIR = "/app/logs"
SHARED_DIR = "/shared"

# Ensure directories exist
os.makedirs(TASK_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(SHARED_DIR, exist_ok=True)

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


def log_translation(user_request, translated_task):
    """Log task translations to a file"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] Translation:\nUser Request: {user_request}\nTranslated Task:\n{json.dumps(translated_task, indent=2)}\n{'='*50}\n"
    with open(TRANSLATION_LOG, 'a') as f:
        f.write(log_entry)


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
        
        # Skip hidden files (starting with .)
        if name.startswith('.'):
            continue
        
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


@app.route("/node/<node_id>/stop", methods=["PUT"])
def stop_node(node_id):
    """Stop/cancel a specific node"""
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        success = task_manager.cancel_node(node_id)
        if success:
            return jsonify({"status": "cancelled", "node_id": node_id})
        else:
            return jsonify({"error": "Node not found or cannot be cancelled"}), 400
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/node/<node_id>/remove", methods=["DELETE"])
def remove_node(node_id):
    """Remove a node from the task tree"""
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        success = task_manager.remove_node(node_id)
        if success:
            return jsonify({"status": "removed", "node_id": node_id})
        else:
            return jsonify({"error": "Node not found or cannot be removed"}), 400
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/node/<node_id>", methods=["GET"])
def get_node_details(node_id):
    """Get detailed information about a specific node"""
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        details = task_manager.get_node_details(node_id)
        if not details:
            return jsonify({"error": "Node not found"}), 404
        
        return jsonify(details)
        
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


@app.route("/reset", methods=["POST"])
def reset():
    """Reset session (kept for compatibility)"""
    return jsonify({"status": "Session reset"})


@app.route("/files", methods=["GET"])
def get_files():
    try:
        path = request.args.get('path', '/app/work')
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)