#!/usr/bin/env python3
"""
mcp_agent.py - MCP Agent with configurable execution limits and timeouts
"""
import docker
import re
import requests
import time
from typing import Tuple
from threading import Lock
from .task_models import TaskModel


class MCPAgent:
    """
    Thread-safe agent with configurable kill-switches and command timeouts.
    """
    
    # Class-level lock for Docker client creation (singleton pattern)
    _docker_client_lock = Lock()
    _docker_client = None
    
    @classmethod
    def _get_docker_client(cls):
        """Get or create Docker client (thread-safe singleton)"""
        if cls._docker_client is None:
            with cls._docker_client_lock:
                if cls._docker_client is None:
                    cls._docker_client = docker.from_env()
        return cls._docker_client
    
    def __init__(
        self,
        container_name: str,
        llm_url: str,
        llm_key: str,
        model: str = "kimi-k2-0905-preview",
        log_callback=None,
        output_callback=None,
        install_log_callback=None
    ):
        self.container_name = container_name
        self.llm_url = llm_url
        self.llm_key = llm_key
        self.model = model
        self.log_callback = log_callback
        self.output_callback = output_callback
        self.install_log_callback = install_log_callback
        
        # Instance-level lock for this agent's operations
        self._lock = Lock()
        
        # Import limits dynamically to avoid circular imports
        from .execution_limits import get_limits, get_metrics
        self.limits = get_limits()
        self.metrics = get_metrics()
    
    def execute_task(self, task: TaskModel, system_prompt: str) -> str:
        """
        Execute a task with configurable iteration limit and stuck-loop detection.
        
        Kill-switches:
        1. Max iterations (default: 20)
        2. Empty output threshold (default: 5 consecutive)
        3. Comment-only threshold (default: 5 consecutive)
        """
        conversation = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Begin working on this task: {task.abstract}"}
        ]
        
        all_output = []
        max_iterations = self.limits.mcp_max_iterations
        empty_threshold = self.limits.mcp_empty_output_threshold
        comment_threshold = self.limits.mcp_comment_only_threshold
        
        empty_output_count = 0
        comment_only_count = 0
        
        for iteration in range(max_iterations):
            # Get next command from LLM
            cmd = self._llm_next_command(conversation)
            conversation.append({"role": "assistant", "content": cmd})
            
            # Log LLM response
            if self.output_callback:
                self.output_callback('llm', cmd)
            
            # Check if done
            if cmd.startswith("DONE:"):
                summary = cmd[5:].strip()
                done_msg = f"\n=== TASK COMPLETED ===\n{summary}\n"
                all_output.append(done_msg)
                if self.output_callback:
                    self.output_callback('terminal', done_msg)
                break
            
            # Detect comment-only commands
            if self._is_comment_only(cmd):
                comment_only_count += 1
                
                feedback = f"[SYSTEM] Your last output was only a comment. Please provide an actual command to execute, or respond with 'DONE: reason' if the task cannot be completed."
                
                if comment_only_count >= comment_threshold:
                    self.metrics.increment('mcp_comment_loops')
                    stuck_msg = f"\n[SYSTEM] Task terminated - stuck in comment-only loop after {comment_threshold} attempts.\n"
                    all_output.append(stuck_msg)
                    if self.output_callback:
                        self.output_callback('terminal', stuck_msg)
                    conversation.append({"role": "user", "content": stuck_msg})
                    break
                
                conversation.append({"role": "user", "content": feedback})
                terminal_output = f"$ {cmd}\n{feedback}\n"
                all_output.append(terminal_output)
                if self.output_callback:
                    self.output_callback('terminal', terminal_output)
                continue
            else:
                comment_only_count = 0
            
            # Execute command with timeout
            output, _ = self._kali_exec(cmd)
            terminal_output = f"$ {cmd}\n{output}\n"
            all_output.append(terminal_output)
            
            # Detect empty/meaningless output
            if len(output.strip()) < 10:
                empty_output_count += 1
            else:
                empty_output_count = 0
            
            # Break if stuck (consecutive empty outputs)
            if empty_output_count >= empty_threshold:
                stuck_msg = f"\n[SYSTEM] Task appears stuck - no meaningful output after {empty_threshold} iterations. If you cannot make progress, respond with 'DONE: Unable to complete - reason'.\n"
                all_output.append(stuck_msg)
                if self.output_callback:
                    self.output_callback('terminal', stuck_msg)
                conversation.append({"role": "user", "content": stuck_msg})
                empty_output_count = 0
            
            # Log terminal output
            if self.output_callback:
                self.output_callback('terminal', terminal_output)
            
            # Add output to conversation
            conversation.append({"role": "user", "content": f"Command output:\n{output}"})
        
        # If we hit max iterations
        if iteration >= max_iterations - 1:
            self.metrics.increment('mcp_iteration_limits')
            timeout_msg = f"\n[SYSTEM] Reached maximum iteration limit ({max_iterations}). Task incomplete.\n"
            all_output.append(timeout_msg)
            if self.output_callback:
                self.output_callback('terminal', timeout_msg)
        
        return "\n".join(all_output)
    
    def _is_comment_only(self, cmd: str) -> bool:
        """Check if command is only comments (no actual executable code)"""
        lines = cmd.strip().split('\n')
        non_comment_lines = [
            line.strip() 
            for line in lines 
            if line.strip() and not line.strip().startswith('#')
        ]
        return len(non_comment_lines) == 0
    
    def _llm_next_command(self, conversation_history: list) -> str:
        """Get next command from LLM with configurable retry logic"""
        max_retries = self.limits.llm_max_retries
        base_delay = self.limits.llm_base_delay
        timeout = self.limits.llm_call_timeout
        
        for attempt in range(max_retries):
            try:
                payload = {
                    "model": self.model,
                    "temperature": 0,
                    "messages": conversation_history
                }
                
                headers = {
                    "Authorization": f"Bearer {self.llm_key}",
                    "Content-Type": "application/json"
                }
                
                response = requests.post(
                    self.llm_url, 
                    headers=headers, 
                    json=payload, 
                    timeout=timeout
                )
                response.raise_for_status()
                
                raw_response = response.json()["choices"][0]["message"]["content"].strip()
                
                if self.log_callback:
                    self.log_callback(f"LLM RAW: {raw_response}")
                
                command = self._extract_command(raw_response)
                
                if self.log_callback:
                    self.log_callback(f"LLM EXTRACTED: {command}")
                
                return command
                
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    self.metrics.increment('llm_rate_limits')
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        print(f"[MCP LLM] Rate limited (429). Retrying in {delay}s... (attempt {attempt + 1}/{max_retries})")
                        if self.log_callback:
                            self.log_callback(f"Rate limited, waiting {delay}s before retry")
                        time.sleep(delay)
                        continue
                    else:
                        self.metrics.increment('llm_failures')
                        raise RuntimeError(f"Failed after {max_retries} attempts due to rate limiting")
                else:
                    self.metrics.increment('llm_failures')
                    raise RuntimeError(f"LLM API error: {e}")
                    
            except (requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"[MCP LLM] Request failed: {e}. Retrying in {delay}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    continue
                else:
                    self.metrics.increment('llm_failures')
                    raise RuntimeError(f"Failed to call LLM API after {max_retries} attempts: {e}")
        
        self.metrics.increment('llm_failures')
        raise RuntimeError("Exhausted all retry attempts")
    
    def _extract_command(self, response: str) -> str:
        """Extract the actual command from LLM response"""
        response = response.strip()
        
        if response.startswith("DONE:"):
            return response
        
        # Remove markdown code blocks
        code_block_pattern = r'```(?:bash|sh)?\s*\n(.*?)\n```'
        matches = re.findall(code_block_pattern, response, re.DOTALL)
        if matches:
            return matches[0].strip()
        
        # Remove explanatory text
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
    
    def _kali_exec(self, cmd: str) -> Tuple[str, bool]:
        """
        Execute command in Kali container with configurable timeout.
        
        Kill-switch: Command timeout (default: 300s)
        """
        with self._lock:
            try:
                client = self._get_docker_client()
                container = client.containers.get(self.container_name)
            except docker.errors.NotFound:
                return f"Error: Container '{self.container_name}' not found", False
            except Exception as e:
                return f"Error: Failed to connect to container: {e}", False
            
            # Execute command with timeout
            start_time = time.time()
            timeout = self.limits.docker_exec_timeout
            
            try:
                # Create exec instance
                exec_id = container.client.api.exec_create(
                    container.id,
                    ["/bin/bash", "-c", cmd],
                    tty=False,
                    stderr=True,
                    stdout=True
                )
                
                # Start execution
                exec_stream = container.client.api.exec_start(
                    exec_id['Id'],
                    stream=True,
                    demux=False
                )
                
                # Collect output with timeout
                output_chunks = []
                for chunk in exec_stream:
                    output_chunks.append(chunk)
                    
                    elapsed = time.time() - start_time
                    
                    # Log slow commands
                    if self.limits.log_slow_commands and elapsed > (timeout * 0.5):
                        if self.log_callback:
                            self.log_callback(f"SLOW COMMAND: {cmd[:100]} - {elapsed:.1f}s elapsed")
                        self.metrics.increment('docker_slow_commands')
                    
                    # Check timeout
                    if elapsed > timeout:
                        self.metrics.increment('docker_timeouts')
                        timeout_msg = f"\n[TIMEOUT] Command exceeded {timeout}s limit and was interrupted.\n"
                        output_chunks.append(timeout_msg.encode())
                        
                        if self.limits.docker_kill_on_timeout:
                            # DANGEROUS: Kill the exec process
                            try:
                                container.client.api.exec_stop(exec_id['Id'])
                            except:
                                pass
                        
                        break
                
                output = b''.join(output_chunks).decode(errors="ignore")
                
            except Exception as e:
                return f"Error executing command: {e}", False
            
            tool_installed = False
            
            # Auto-install missing tools
            if "command not found" in output:
                match = re.search(r"bash:.*?:\s*(\w+):\s*command not found", output)
                if match:
                    missing_tool = match.group(1)
                    
                    if self.install_log_callback:
                        self.install_log_callback(missing_tool)
                    
                    install_cmd = f"apt-get update && apt-get install -y {missing_tool}"
                    
                    try:
                        install_result = container.exec_run(
                            ["/bin/bash", "-c", install_cmd],
                            tty=False,
                            stderr=True,
                            stdout=True
                        )
                        
                        if install_result.exit_code == 0:
                            tool_installed = True
                            
                            raw = container.exec_run(
                                ["/bin/bash", "-c", cmd],
                                tty=False,
                                stderr=True,
                                stdout=True
                            )
                            output = f"[System] Tool '{missing_tool}' was not found. Automatically installed it.\n\n" + raw.output.decode(errors="ignore")
                        else:
                            output = f"[System] Tool '{missing_tool}' was not found and could not be installed automatically.\n\n{output}"
                    
                    except Exception as e:
                        output = f"[System] Tool '{missing_tool}' was not found. Installation failed: {e}\n\n{output}"
            
            if self.log_callback:
                self.log_callback(f"EXEC: {cmd}\nOUTPUT: {output[:500]}...")
            
            return output, tool_installed
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test if the agent can connect to the Kali container"""
        try:
            client = self._get_docker_client()
            container = client.containers.get(self.container_name)
            
            result = container.exec_run(
                ["/bin/bash", "-c", "echo 'Connection test successful'"],
                tty=False,
                stderr=True,
                stdout=True
            )
            
            if result.exit_code == 0:
                return True, "Successfully connected to Kali container"
            else:
                return False, f"Container found but command failed: {result.output.decode()}"
        
        except docker.errors.NotFound:
            return False, f"Container '{self.container_name}' not found"
        except Exception as e:
            return False, f"Connection error: {str(e)}"
    
    def get_container_info(self) -> dict:
        """Get information about the Kali container"""
        try:
            client = self._get_docker_client()
            container = client.containers.get(self.container_name)
            
            return {
                "name": container.name,
                "status": container.status,
                "image": container.image.tags[0] if container.image.tags else "unknown",
                "created": container.attrs.get("Created", "unknown"),
                "id": container.short_id
            }
        except Exception as e:
            return {"error": str(e)}
    
    def execute_single_command(self, cmd: str) -> str:
        """Execute a single command without LLM interaction"""
        output, _ = self._kali_exec(cmd)
        return output
    
    def create_file(self, path: str, content: str) -> Tuple[bool, str]:
        """Create a file in the container with the given content"""
        escaped_content = content.replace("'", "'\\''")
        cmd = f"echo '{escaped_content}' > {path}"
        output, _ = self._kali_exec(cmd)
        
        verify_cmd = f"test -f {path} && echo 'SUCCESS' || echo 'FAILED'"
        verify_output, _ = self._kali_exec(verify_cmd)
        
        if "SUCCESS" in verify_output:
            return True, f"File created successfully at {path}"
        else:
            return False, f"Failed to create file: {output}"
    
    def read_file(self, path: str) -> Tuple[bool, str]:
        """Read a file from the container"""
        check_cmd = f"test -f {path} && echo 'EXISTS' || echo 'NOT_FOUND'"
        check_output, _ = self._kali_exec(check_cmd)
        
        if "NOT_FOUND" in check_output:
            return False, f"File not found: {path}"
        
        read_cmd = f"cat {path}"
        content, _ = self._kali_exec(read_cmd)
        
        return True, content
    
    def list_directory(self, path: str) -> Tuple[bool, list]:
        """List contents of a directory"""
        check_cmd = f"test -d {path} && echo 'EXISTS' || echo 'NOT_FOUND'"
        check_output, _ = self._kali_exec(check_cmd)
        
        if "NOT_FOUND" in check_output:
            return False, [f"Directory not found: {path}"]
        
        list_cmd = f"ls -la {path}"
        output, _ = self._kali_exec(list_cmd)
        
        lines = output.strip().split('\n')
        files = []
        
        for line in lines[1:]:
            if line.strip():
                files.append(line)
        
        return True, files