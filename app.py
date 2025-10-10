#!/usr/bin/env python3
"""
app.py - Task management API with 4-direction graph support, automatic status reconciliation,
and configurable execution limits
"""
import os
import docker
import traceback
import sys
from flask import Flask, request, render_template, jsonify
import datetime
import json
import threading
import time

# Import task management system
try:
    from src.task_translator import create_translator, TaskModel
    from src.task_manager import TaskManager
    from src.parallel_config import init_parallel_config, get_config
    from src.execution_limits import (
        init_execution_limits, get_limits, set_limits, 
        get_metrics, ExecutionLimits
    )
    print("✓ Task management modules imported successfully")
except ImportError as e:
    print(f"✗ Failed to import task management modules: {e}")
    traceback.print_exc()
    sys.exit(1)

app = Flask(__name__)

app.static_folder = 'static'
app.static_url_path = '/static'
LLM_URL = os.getenv("LLM_BASE_URL", "https://api.moonshot.ai/v1") + "/chat/completions"
LLM_KEY = os.getenv("MOONSHOT_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "kimi-k2-0905-preview")
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

# Initialize parallel configuration
try:
    parallel_config = init_parallel_config()
    print("✓ Parallel configuration initialized")
    print(parallel_config)
except Exception as e:
    print(f"✗ Warning: Parallel configuration initialization failed: {e}")
    traceback.print_exc()

# Initialize execution limits
try:
    execution_limits = init_execution_limits()
    print("✓ Execution limits initialized")
    print(execution_limits)
except Exception as e:
    print(f"✗ Warning: Execution limits initialization failed: {e}")
    traceback.print_exc()

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


def reconcile_node_status():
    """
    Background task: Reconcile status across all nodes every 5 minutes.
    
    Scans all node logs for completion markers (DONE:) and updates status
    if mismatched between logs and TaskManager state.
    """
    while True:
        try:
            time.sleep(300)  # 5 minutes
            
            if not task_manager:
                continue
            
            reconciled_count = 0
            
            with task_manager.nodes_lock:
                for node_id, node_data in list(task_manager.nodes.items()):
                    current_status = node_data['status']
                    
                    # Skip already completed/failed/cancelled
                    if current_status in ['completed', 'failed', 'cancelled', 'impossible']:
                        continue
                    
                    # Check log for completion marker
                    log_content = task_manager.get_node_log(node_id)
                    
                    if log_content and 'DONE:' in log_content:
                        # Log shows completion but status is stuck
                        task_id = node_data['task_id']
                        
                        print(f"[RECONCILE] Node {node_id} stuck at '{current_status}' but log shows DONE")
                        
                        # Update self.nodes
                        task_manager.nodes[node_id]['status'] = 'completed'
                        task_manager.nodes[node_id]['completed_at'] = datetime.datetime.now()
                        
                        # Sync to TRM
                        with task_manager.trms_lock:
                            if task_id in task_manager.trms:
                                trm = task_manager.trms[task_id]
                                trm.update_node_status(node_id, 'completed')
                        
                        reconciled_count += 1
                        print(f"[RECONCILE] ✓ Node {node_id} status updated to 'completed'")
            
            if reconciled_count > 0:
                print(f"[RECONCILE] Reconciled {reconciled_count} stuck node(s)")
                
        except Exception as e:
            print(f"[RECONCILE] Error during reconciliation: {e}")
            traceback.print_exc()


# Start background reconciliation thread
reconcile_thread = threading.Thread(target=reconcile_node_status, daemon=True)
reconcile_thread.start()
print("✓ Background status reconciliation started (runs every 5 minutes)")


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
    return render_template('index.html')



@app.route("/config", methods=["GET"])
def get_parallel_config():
    """Get current parallel execution configuration"""
    try:
        config = get_config()
        return jsonify({
            "enabled": config.enabled,
            "max_workers": config.max_workers,
            "max_llm_concurrent": config.max_llm_concurrent,
            "llm_max_retries": config.llm_max_retries,
            "llm_base_delay": config.llm_base_delay,
            "docker_timeout": config.docker_timeout,
            "use_node_prefixes": config.use_node_prefixes
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/config", methods=["PUT"])
def update_parallel_config():
    """Update parallel execution configuration"""
    try:
        from src.parallel_config import ParallelConfig, set_config
        
        data = request.json
        config = ParallelConfig(
            enabled=data.get("enabled", True),
            max_workers=data.get("max_workers", 5),
            max_llm_concurrent=data.get("max_llm_concurrent", 3),
            llm_max_retries=data.get("llm_max_retries", 5),
            llm_base_delay=data.get("llm_base_delay", 2),
            docker_timeout=data.get("docker_timeout", 90),
            use_node_prefixes=data.get("use_node_prefixes", True)
        )
        
        set_config(config)
        
        return jsonify({
            "status": "updated",
            "config": {
                "enabled": config.enabled,
                "max_workers": config.max_workers,
                "max_llm_concurrent": config.max_llm_concurrent
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/limits", methods=["GET"])
def get_execution_limits():
    """Get current execution limits configuration"""
    try:
        limits = get_limits()
        return jsonify(limits.to_dict())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/limits", methods=["PUT"])
def update_execution_limits():
    """
    Update execution limits configuration.
    
    Example body:
    {
        "mcp": {
            "max_iterations": 30,
            "command_timeout": 600
        },
        "docker": {
            "exec_timeout": 600
        }
    }
    """
    try:
        data = request.json
        limits = ExecutionLimits.from_dict(data)
        set_limits(limits)
        
        return jsonify({
            "status": "updated",
            "limits": limits.to_dict()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/metrics", methods=["GET"])
def get_execution_metrics():
    """
    Get execution metrics (kill-switch hit counts).
    
    Tracks how often each soft kill-switch was triggered:
    - MCP iteration limits
    - LLM rate limits
    - Task retry exhaustion
    - Docker command timeouts
    """
    try:
        metrics = get_metrics()
        return jsonify(metrics.to_dict())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/metrics", methods=["DELETE"])
def reset_execution_metrics():
    """Reset execution metrics counters"""
    try:
        metrics = get_metrics()
        metrics.reset()
        return jsonify({"status": "reset"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health_check():
    """
    Comprehensive health check.
    
    Returns:
    - Execution limits configuration
    - Current metrics
    - Task queue status
    - Container connection status
    """
    try:
        from src.task_models import TaskStatus
        
        limits = get_limits()
        metrics = get_metrics()
        
        # Test container connection
        container_ok = False
        container_msg = "Task manager not initialized"
        
        if task_manager:
            try:
                # Use the task_manager's mcp_client for health check
                container_ok, container_msg = task_manager.mcp_client.test_connection()
            except Exception as e:
                container_ok = False
                container_msg = str(e)
        
        # Count active tasks
        active_tasks = 0
        total_tasks = 0
        
        if task_manager:
            total_tasks = len(task_manager.tasks)
            active_tasks = sum(
                1 for info in task_manager.tasks.values()
                if info['status'] in [TaskStatus.PENDING, TaskStatus.PLANNING, TaskStatus.WORKING]
            )
        
        # Get executor status
        executor_status = {}
        if task_manager:
            try:
                executor_status = task_manager.get_executor_status()
            except:
                pass
        
        return jsonify({
            "status": "healthy" if container_ok else "degraded",
            "container": {
                "connected": container_ok,
                "message": container_msg
            },
            "limits": limits.to_dict(),
            "metrics": metrics.to_dict(),
            "tasks": {
                "active": active_tasks,
                "total": total_tasks
            },
            "executor": executor_status
        })
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 500


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
        
        translated_task = task_translator.translate_task(user_request)
        task_dict = translated_task.model_dump()
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
        
        try:
            task = TaskModel(**translated_task)
        except Exception as e:
            return jsonify({"error": f"Invalid task structure: {e}"}), 400
        
        task_id = task_manager.create_task(task)
        
        return jsonify({
            "task_id": task_id,
            "status": "pending",
            "message": "Task created and queued for execution",
            "parallel_enabled": get_config().enabled
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
        
        return jsonify({"tasks": tasks})
        
    except Exception as e:
        print(f"[API] Error in get_all_tasks: {e}")
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
        
        return jsonify(status)
        
    except Exception as e:
        print(f"[API] Error getting task status: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/task/<task_id>/nodes", methods=["GET"])
def get_task_nodes(task_id):
    """Get hierarchical list of all nodes for a task"""
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        nodes = task_manager.get_task_nodes(task_id)
        
        return jsonify({"nodes": nodes})
        
    except Exception as e:
        print(f"[API] Error getting task nodes: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/task/<task_id>/cancel", methods=["PUT"])
def cancel_task(task_id):
    """Cancel a running task and all its nodes"""
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        success = task_manager.cancel_task(task_id)
        if success:
            # Track cancellation metric
            metrics = get_metrics()
            metrics.increment('cancellations')
            
            return jsonify({"status": "cancelled", "task_id": task_id})
        else:
            return jsonify({"error": "Task not found or cannot be cancelled"}), 400
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/task/<task_id>/complete", methods=["PUT"])
def complete_task(task_id):
    """Mark a task as completed"""
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        success = task_manager.mark_task_complete(task_id)
        if success:
            return jsonify({"status": "completed", "task_id": task_id})
        else:
            return jsonify({"error": "Task not found or cannot be completed"}), 400
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/task/<task_id>/restart", methods=["POST"])
def restart_task(task_id):
    """Restart a task with optional improvement comments"""
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        data = request.json or {}
        comments = data.get("comments", "")
        
        new_task_id = task_manager.restart_task(task_id, comments)
        if new_task_id:
            return jsonify({
                "status": "restarted",
                "old_task_id": task_id,
                "new_task_id": new_task_id,
                "message": "Task restarted with improvements" if comments else "Task restarted"
            })
        else:
            return jsonify({"error": "Task not found or cannot be restarted"}), 400
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/node/<node_id>/cancel", methods=["PUT"])
def cancel_node(node_id):
    """Cancel a specific node (keeps in tree but stops execution)"""
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        success = task_manager.cancel_node(node_id)
        if success:
            # Track cancellation metric
            metrics = get_metrics()
            metrics.increment('cancellations')
            
            return jsonify({"status": "cancelled", "node_id": node_id})
        else:
            return jsonify({"error": "Node not found or cannot be cancelled"}), 400
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/node/<node_id>/complete", methods=["PUT"])
def complete_node(node_id):
    """Mark a node as completed manually"""
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        success = task_manager.mark_node_complete(node_id)
        if success:
            return jsonify({"status": "completed", "node_id": node_id})
        else:
            return jsonify({"error": "Node not found or cannot be completed"}), 400
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/node/<node_id>/start", methods=["POST"])
def force_start_node(node_id):
    """Force start a pending/cancelled node"""
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        success = task_manager.force_start_node(node_id)
        if success:
            return jsonify({"status": "started", "node_id": node_id})
        else:
            return jsonify({"error": "Node not found or cannot be started"}), 400
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/node/<node_id>/restart", methods=["POST"])
def restart_node(node_id):
    """Restart a node with optional improvement comments"""
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        data = request.json or {}
        comments = data.get("comments", "")
        
        new_node_id = task_manager.restart_node(node_id, comments)
        if new_node_id:
            return jsonify({
                "status": "restarted",
                "old_node_id": node_id,
                "new_node_id": new_node_id,
                "message": "Node restarted with improvements" if comments else "Node restarted"
            })
        else:
            return jsonify({"error": "Node not found or cannot be restarted"}), 400
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/node/<node_id>/remove", methods=["DELETE"])
def remove_node(node_id):
    """Remove a node and its entire subtree from the task tree"""
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


@app.route("/node/<node_id>/log", methods=["GET"])
def get_node_log(node_id):
    """Get log file content for a specific node"""
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        log_content = task_manager.get_node_log(node_id)
        if log_content is None:
            return jsonify({"error": "Node not found"}), 404
        
        return jsonify({
            "node_id": node_id,
            "log": log_content
        })
        
    except Exception as e:
        print(f"[API] Error getting node log: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ========== 4-DIRECTION GRAPH ENDPOINTS ==========

@app.route("/node/<node_id>/rescope", methods=["POST"])
def rescope_node(node_id):
    """
    Re-scope: move node to different parent (UP navigation).
    
    Body: {
        "new_parent_id": "n123456",
        "reason": "Failed exploit, trying lateral movement"
    }
    """
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        data = request.json
        new_parent_id = data.get("new_parent_id")
        reason = data.get("reason", "")
        
        if not new_parent_id:
            return jsonify({"error": "new_parent_id required"}), 400
        
        # Get node's task_id
        with task_manager.nodes_lock:
            if node_id not in task_manager.nodes:
                return jsonify({"error": "Node not found"}), 404
            task_id = task_manager.nodes[node_id]['task_id']
        
        # Get TRM
        with task_manager.trms_lock:
            if task_id not in task_manager.trms:
                return jsonify({"error": "Task not found"}), 404
            trm = task_manager.trms[task_id]
        
        # Perform re-scope using 4-direction graph
        trm.move_node_to_new_parent(node_id, new_parent_id, reason)
        
        return jsonify({
            "status": "rescoped",
            "node_id": node_id,
            "new_parent_id": new_parent_id,
            "reason": reason
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/node/<node_id>/add-variant", methods=["POST"])
def add_variant_node(node_id):
    """
    Add variant as right sibling (RIGHT navigation).
    
    Body: {
        "abstract": "sqlmap with tamper scripts",
        "description": "Run sqlmap --tamper=space2comment",
        "verification": "SQLi successful with tamper"
    }
    """
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        data = request.json
        abstract = data.get("abstract")
        description = data.get("description")
        verification = data.get("verification")
        
        if not all([abstract, description, verification]):
            return jsonify({"error": "abstract, description, verification required"}), 400
        
        # Get node's task_id
        with task_manager.nodes_lock:
            if node_id not in task_manager.nodes:
                return jsonify({"error": "Node not found"}), 404
            task_id = task_manager.nodes[node_id]['task_id']
        
        # Get TRM
        with task_manager.trms_lock:
            if task_id not in task_manager.trms:
                return jsonify({"error": "Task not found"}), 404
            trm = task_manager.trms[task_id]
        
        # Generate variant node ID
        variant_node_id = trm.generate_node_id()
        
        # Add variant using 4-direction graph
        trm.add_sibling_variant(node_id, variant_node_id, abstract, description)
        
        # Register with task manager
        task_manager.register_node(
            task_id=task_id,
            node_id=variant_node_id,
            node_info={
                'abstract': abstract,
                'parent_id': trm.graph.get_parent(node_id),
                'status': 'pending'
            }
        )
        
        return jsonify({
            "status": "variant_added",
            "reference_node_id": node_id,
            "variant_node_id": variant_node_id
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/node/<node_id>/credentials", methods=["GET"])
def get_node_credentials(node_id):
    """
    Get credential chain: all previous nodes that may have credentials.
    """
    try:
        if not task_manager:
            return jsonify({"error": "Task manager not initialized"}), 500
        
        # Get node's task_id
        with task_manager.nodes_lock:
            if node_id not in task_manager.nodes:
                return jsonify({"error": "Node not found"}), 404
            task_id = task_manager.nodes[node_id]['task_id']
        
        # Get TRM
        with task_manager.trms_lock:
            if task_id not in task_manager.trms:
                return jsonify({"error": "Task not found"}), 404
            trm = task_manager.trms[task_id]
        
        # Get credential chain using 4-direction traversal
        cred_chain = trm.get_credential_chain(node_id)
        
        return jsonify({
            "node_id": node_id,
            "credential_sources": cred_chain
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ========== END 4-DIRECTION GRAPH ENDPOINTS ==========


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