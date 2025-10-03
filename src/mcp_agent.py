#!/usr/bin/env python3
"""
mcp_agent.py - MCP (Model Context Protocol) Agent for task execution
Wraps the existing Kali container execution logic
"""
import docker
import re
import requests
from typing import Tuple
from .task_models import TaskModel


class MCPAgent:
    """
    Agent that executes tasks by running commands in Kali container.
    Wraps the Docker execution logic and provides conversation-based execution.
    """
    
    def __init__(
        self,
        container_name: str,
        llm_url: str,
        llm_key: str,
        model: str = "moonshot-v1-8k",
        log_callback=None,
        output_callback=None,  # New callback for terminal output
        install_log_callback=None  # Callback for tool installations
    ):
        self.container_name = container_name
        self.llm_url = llm_url
        self.llm_key = llm_key
        self.model = model
        self.log_callback = log_callback
        self.output_callback = output_callback  # Stores terminal/LLM output
        self.install_log_callback = install_log_callback  # Logs installations
    
    def execute_task(self, task: TaskModel, system_prompt: str) -> str:
        """
        Execute a task by having an LLM conversation with command execution.
        Returns the accumulated output.
        """
        conversation = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Begin working on this task: {task.abstract}"}
        ]
        
        all_output = []
        max_iterations = 20  # Safety limit
        
        for iteration in range(max_iterations):
            # Get next command from LLM
            cmd = self._llm_next_command(conversation)
            conversation.append({"role": "assistant", "content": cmd})
            
            # Log LLM response (do this BEFORE checking if done)
            if self.output_callback:
                self.output_callback('llm', cmd)
                print(f"[MCP] Logged LLM response: {cmd[:100]}...")
            
            # Check if done
            if cmd.startswith("DONE:"):
                summary = cmd[5:].strip()
                done_msg = f"\n=== TASK COMPLETED ===\n{summary}\n"
                all_output.append(done_msg)
                if self.output_callback:
                    self.output_callback('terminal', done_msg)
                    print(f"[MCP] Logged completion message")
                break
            
            # Execute command
            output, _ = self._kali_exec(cmd)
            terminal_output = f"$ {cmd}\n{output}\n"
            all_output.append(terminal_output)
            
            # Log terminal output
            if self.output_callback:
                self.output_callback('terminal', terminal_output)
                print(f"[MCP] Logged terminal output: {len(terminal_output)} chars")
            
            # Add output to conversation
            conversation.append({
                "role": "user",
                "content": f"Command output:\n{output}"
            })
        
        return "\n".join(all_output)
    
    def _llm_next_command(self, conversation_history: list) -> str:
        """Get next command from LLM with retry logic"""
        import time
        
        max_retries = 5
        base_delay = 2
        
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
                
                response = requests.post(self.llm_url, headers=headers, json=payload, timeout=90)
                response.raise_for_status()
                
                raw_response = response.json()["choices"][0]["message"]["content"].strip()
                
                if self.log_callback:
                    self.log_callback(f"LLM RAW: {raw_response}")
                
                # Extract actual command
                command = self._extract_command(raw_response)
                
                if self.log_callback:
                    self.log_callback(f"LLM EXTRACTED: {command}")
                
                return command
                
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:  # Rate limit
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        print(f"[MCP LLM] Rate limited (429). Retrying in {delay}s... (attempt {attempt + 1}/{max_retries})")
                        if self.log_callback:
                            self.log_callback(f"Rate limited, waiting {delay}s before retry")
                        time.sleep(delay)
                        continue
                    else:
                        raise RuntimeError(f"Failed after {max_retries} attempts due to rate limiting")
                else:
                    raise RuntimeError(f"LLM API error: {e}")
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"[MCP LLM] Request failed: {e}. Retrying in {delay}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    continue
                else:
                    raise RuntimeError(f"Failed to call LLM API after {max_retries} attempts: {e}")
        
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
        """Execute command in Kali container"""
        try:
            container = docker.from_env().containers.get(self.container_name)
        except docker.errors.NotFound:
            return f"Error: Container '{self.container_name}' not found", False
        except Exception as e:
            return f"Error: Failed to connect to container: {e}", False
        
        # Execute command
        try:
            raw = container.exec_run(
                ["/bin/bash", "-c", cmd],
                tty=False,
                stderr=True,
                stdout=True
            )
            output = raw.output.decode(errors="ignore")
        except Exception as e:
            return f"Error executing command: {e}", False
        
        tool_installed = False
        
        # Auto-install missing tools
        if "command not found" in output:
            match = re.search(r"bash:.*?:\s*(\w+):\s*command not found", output)
            if match:
                missing_tool = match.group(1)
                
                # Log the installation
                if self.install_log_callback:
                    self.install_log_callback(missing_tool)
                
                # Try to install the missing tool
                install_cmd = f"apt-get update && apt-get install -y {missing_tool}"
                
                try:
                    install_result = container.exec_run(
                        ["/bin/bash", "-c", install_cmd],
                        tty=False,
                        stderr=True,
                        stdout=True
                    )
                    
                    # Check if installation was successful
                    if install_result.exit_code == 0:
                        tool_installed = True
                        
                        # Re-run original command
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
            self.log_callback(f"EXEC: {cmd}\nOUTPUT: {output[:500]}...")  # Log first 500 chars
        
        return output, tool_installed
    
    def test_connection(self) -> Tuple[bool, str]:
        """
        Test if the agent can connect to the Kali container.
        Returns (success, message)
        """
        try:
            container = docker.from_env().containers.get(self.container_name)
            
            # Try a simple command
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
            container = docker.from_env().containers.get(self.container_name)
            
            return {
                "name": container.name,
                "status": container.status,
                "image": container.image.tags[0] if container.image.tags else "unknown",
                "created": container.attrs.get("Created", "unknown"),
                "id": container.short_id
            }
        except Exception as e:
            return {
                "error": str(e)
            }
    
    def execute_single_command(self, cmd: str) -> str:
        """
        Execute a single command without LLM interaction.
        Useful for testing or direct execution.
        """
        output, _ = self._kali_exec(cmd)
        return output
    
    def create_file(self, path: str, content: str) -> Tuple[bool, str]:
        """
        Create a file in the container with the given content.
        Returns (success, message)
        """
        # Escape single quotes in content
        escaped_content = content.replace("'", "'\\''")
        
        # Use echo with redirection
        cmd = f"echo '{escaped_content}' > {path}"
        
        output, _ = self._kali_exec(cmd)
        
        # Verify file was created
        verify_cmd = f"test -f {path} && echo 'SUCCESS' || echo 'FAILED'"
        verify_output, _ = self._kali_exec(verify_cmd)
        
        if "SUCCESS" in verify_output:
            return True, f"File created successfully at {path}"
        else:
            return False, f"Failed to create file: {output}"
    
    def read_file(self, path: str) -> Tuple[bool, str]:
        """
        Read a file from the container.
        Returns (success, content_or_error)
        """
        # Check if file exists
        check_cmd = f"test -f {path} && echo 'EXISTS' || echo 'NOT_FOUND'"
        check_output, _ = self._kali_exec(check_cmd)
        
        if "NOT_FOUND" in check_output:
            return False, f"File not found: {path}"
        
        # Read file content
        read_cmd = f"cat {path}"
        content, _ = self._kali_exec(read_cmd)
        
        return True, content
    
    def list_directory(self, path: str) -> Tuple[bool, list]:
        """
        List contents of a directory.
        Returns (success, list_of_files_or_error)
        """
        # Check if directory exists
        check_cmd = f"test -d {path} && echo 'EXISTS' || echo 'NOT_FOUND'"
        check_output, _ = self._kali_exec(check_cmd)
        
        if "NOT_FOUND" in check_output:
            return False, [f"Directory not found: {path}"]
        
        # List directory
        list_cmd = f"ls -la {path}"
        output, _ = self._kali_exec(list_cmd)
        
        # Parse output into list
        lines = output.strip().split('\n')
        files = []
        
        for line in lines[1:]:  # Skip first line (total)
            if line.strip():
                files.append(line)
        
        return True, files