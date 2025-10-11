/* =================================================================
   Kali-LLM Task Manager - With Assistant/Automation Mode Toggle
   ================================================================= */

// Initialize Mermaid
mermaid.initialize({ startOnLoad: false, theme: 'dark' });

// Global state
let currentMode = 'assistant'; // 'assistant' or 'automation'
let task = null;
let orig = null;
let curPath = '/app/work';
let curTask = null;
let intervals = { graph: null, output: null, poll: null, outputNodes: null };
let auto = { graph: false, output: false };
let nodeCache = {};
let improveTaskId = null;
let improveNodeId = null;
let graphScrollPos = { tasks: 0, graph: 0 };

// DOM element references
let input, sendBtn, resetBtn, taskCard, abstract, description, verification;
let executeTaskBtn, cancelTaskBtn, chatMessages;
let assistantMode, automationMode, toggleSlider;
let graphId, outputTaskId, outputNodeSelect;
let graphView, outputView, fileGrid, fileView, path, upBtn, taskList, taskGraph, taskGraphView, count;
let refreshTasksBtn, loadGraphBtn, loadOutputBtn, graphAuto, outputAuto;
let navWork, navLogs, navShared, tasksViewSelect;
let improveTaskModal, improveTaskComments, improveTaskCancelBtn, improveTaskSubmitBtn;
let improveNodeModal, improveNodeComments, improveNodeCancelBtn, improveNodeSubmitBtn;

// =================================================================
// Utility Functions
// =================================================================

function msg(text, color = '#2196f3', isHTML = false) {
    // Hide completion messages
    if (text.includes('✅ Request completed') || text.includes('✅ Task')) {
        return;
    }
    
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message system-message';
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    if (isHTML) {
        contentDiv.innerHTML = text;
    } else {
        contentDiv.textContent = text;
    }
    
    msgDiv.appendChild(contentDiv);
    
    chatMessages.appendChild(msgDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function formatTimestamp(date) {
    const hours = date.getHours().toString().padStart(2, '0');
    const minutes = date.getMinutes().toString().padStart(2, '0');
    const day = date.getDate();
    const month = date.toLocaleString('en', { month: 'short' });
    return `${hours}:${minutes}, ${day} ${month}`;
}

function formatTerminalOutput(commands) {
    let html = '<div class="terminal-output">';
    
    const parts = commands.split('$').filter(p => p.trim());
    
    parts.forEach(part => {
        const lines = part.trim().split('\n');
        const command = lines[0].trim();
        const output = lines.slice(1).join('\n').trim();
        
        html += '<div class="command-line">$ ' + esc(command) + '</div>';
        
        if (output) {
            // Check for special message types
            if (output.includes('[System]') || output.includes('Automatically installed')) {
                html += '<div class="command-output install-message">' + esc(output) + '</div>';
            } else if (output.includes('✅') || output.includes('DONE:')) {
                html += '<div class="command-output success-message">' + esc(output) + '</div>';
            } else if (output.includes('❌') || output.includes('Error')) {
                html += '<div class="command-output error-message">' + esc(output) + '</div>';
            } else {
                html += '<div class="command-output">' + esc(output) + '</div>';
            }
        }
    });
    
    html += '</div>';
    return html;
}

function addUserMessage(text) {
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message user-message';
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.textContent = text;
    
    const timestamp = document.createElement('span');
    timestamp.className = 'timestamp';
    timestamp.textContent = formatTimestamp(new Date());
    
    msgDiv.appendChild(contentDiv);
    msgDiv.appendChild(timestamp);
    
    chatMessages.appendChild(msgDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function addAssistantMessage(text, isHTML = false) {
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message assistant-message';
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    if (isHTML) {
        contentDiv.innerHTML = text;
    } else {
        contentDiv.textContent = text;
    }
    
    const timestamp = document.createElement('span');
    timestamp.className = 'timestamp';
    timestamp.textContent = formatTimestamp(new Date());
    
    msgDiv.appendChild(contentDiv);
    msgDiv.appendChild(timestamp);
    
    chatMessages.appendChild(msgDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function esc(t) {
    const d = document.createElement('div');
    d.textContent = t;
    return d.innerHTML;
}

// =================================================================
// Initialization
// =================================================================

document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM loaded, initializing...');
    
    // Initialize all element references
    input = document.getElementById('input');
    sendBtn = document.getElementById('sendBtn');
    resetBtn = document.getElementById('resetBtn');
    taskCard = document.getElementById('taskCard');
    abstract = document.getElementById('abstract');
    description = document.getElementById('description');
    verification = document.getElementById('verification');
    executeTaskBtn = document.getElementById('executeTaskBtn');
    cancelTaskBtn = document.getElementById('cancelTaskBtn');
    chatMessages = document.getElementById('chatMessages');
    assistantMode = document.getElementById('assistantMode');
    automationMode = document.getElementById('automationMode');
    toggleSlider = document.getElementById('toggleSlider');
    graphId = document.getElementById('graphId');
    outputTaskId = document.getElementById('outputTaskId');
    outputNodeSelect = document.getElementById('outputNodeSelect');
    graphView = document.getElementById('graphView');
    outputView = document.getElementById('outputView');
    fileGrid = document.getElementById('fileGrid');
    fileView = document.getElementById('fileView');
    path = document.getElementById('path');
    upBtn = document.getElementById('upBtn');
    taskList = document.getElementById('taskList');
    taskGraph = document.getElementById('taskGraph');
    taskGraphView = document.getElementById('taskGraphView');
    count = document.getElementById('count');
    refreshTasksBtn = document.getElementById('refreshTasksBtn');
    loadGraphBtn = document.getElementById('loadGraphBtn');
    loadOutputBtn = document.getElementById('loadOutputBtn');
    graphAuto = document.getElementById('graphAuto');
    outputAuto = document.getElementById('outputAuto');
    navWork = document.getElementById('navWork');
    navLogs = document.getElementById('navLogs');
    navShared = document.getElementById('navShared');
    tasksViewSelect = document.getElementById('tasksViewSelect');
    improveTaskModal = document.getElementById('improveTaskModal');
    improveTaskComments = document.getElementById('improveTaskComments');
    improveTaskCancelBtn = document.getElementById('improveTaskCancelBtn');
    improveTaskSubmitBtn = document.getElementById('improveTaskSubmitBtn');
    improveNodeModal = document.getElementById('improveNodeModal');
    improveNodeComments = document.getElementById('improveNodeComments');
    improveNodeCancelBtn = document.getElementById('improveNodeCancelBtn');
    improveNodeSubmitBtn = document.getElementById('improveNodeSubmitBtn');
    
    console.log('Elements initialized');
    
    // Add event listeners
    input.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    });
    
    sendBtn.addEventListener('click', handleSend);
    resetBtn.addEventListener('click', handleReset);
    executeTaskBtn.addEventListener('click', executeStructuredTask);
    cancelTaskBtn.addEventListener('click', cancelTaskConfig);
    
    // Mode toggle
    assistantMode.addEventListener('click', () => setMode('assistant'));
    automationMode.addEventListener('click', () => setMode('automation'));
    
    // Other event listeners
    refreshTasksBtn.addEventListener('click', loadTasks);
    loadGraphBtn.addEventListener('click', loadGraph);
    loadOutputBtn.addEventListener('click', loadOutput);
    graphAuto.addEventListener('click', () => toggleAuto('graph'));
    outputAuto.addEventListener('click', () => toggleAuto('output'));
    upBtn.addEventListener('click', goUp);
    navWork.addEventListener('click', () => loadFiles('/app/work'));
    navLogs.addEventListener('click', () => loadFiles('/app/logs'));
    navShared.addEventListener('click', () => loadFiles('/app/shared'));
    tasksViewSelect.addEventListener('change', switchTasksView);
    
    // Modal listeners
    improveTaskCancelBtn.addEventListener('click', () => {
        improveTaskModal.classList.remove('active');
        improveTaskId = null;
    });
    improveTaskSubmitBtn.addEventListener('click', submitTaskImprovement);
    
    improveNodeCancelBtn.addEventListener('click', () => {
        improveNodeModal.classList.remove('active');
        improveNodeId = null;
    });
    improveNodeSubmitBtn.addEventListener('click', submitNodeImprovement);
    
    // Task ID change listeners
    outputTaskId.addEventListener('change', loadOutputNodes);
    outputNodeSelect.addEventListener('change', loadOutput);
    
    // Tab switching
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', function() {
            const tabName = this.getAttribute('data-tab');
            switchTab(tabName);
        });
    });
    
    // Initialize mode toggle
    setMode('assistant');
    
    console.log('Event listeners attached');
    
    // Load initial tasks list
    loadTasks();
});

// =================================================================
// Mode Toggle
// =================================================================

function setMode(mode) {
    currentMode = mode;
    
    if (mode === 'assistant') {
        assistantMode.classList.add('active');
        automationMode.classList.remove('active');
        toggleSlider.style.left = '0%';
    } else {
        assistantMode.classList.remove('active');
        automationMode.classList.add('active');
        toggleSlider.style.left = '50%';
    }
}

// =================================================================
// Tab Management
// =================================================================

function switchTab(name) {
    console.log(`Switching to tab: ${name}`);
    
    // Remove active class from all tabs and content
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    
    // Add active class to selected tab and content
    const selectedTab = document.querySelector(`.tab[data-tab="${name}"]`);
    const selectedContent = document.getElementById(name);
    
    if (selectedTab) selectedTab.classList.add('active');
    if (selectedContent) selectedContent.classList.add('active');
    
    // Load data for specific tabs
    if (name === 'files') {
        loadFiles(curPath);
    } else if (name === 'tasks') {
        loadTasks();
    } else if (name === 'graph' && graphId.value) {
        loadGraph();
    } else if (name === 'output' && outputTaskId.value) {
        loadOutput();
    }
}

function switchTasksView() {
    const view = tasksViewSelect.value;
    if (view === 'graph') {
        taskList.style.display = 'none';
        taskGraph.style.display = 'block';
        loadTasksGraph();
    } else {
        taskList.style.display = 'block';
        taskGraph.style.display = 'none';
    }
}

// =================================================================
// Message Handling
// =================================================================

async function handleSend() {
    const txt = input.value.trim();
    if (!txt) {
        msg('⚠ Enter a request', '#ff9800');
        return;
    }
    
    // Add user message
    addUserMessage(txt);
    
    // Clear input
    input.value = '';
    
    // Disable send button
    sendBtn.disabled = true;
    
    // For automation mode, use the regular endpoint
    if (currentMode === 'automation') {
        try {
            const r = await fetch('/execute', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    message: txt,
                    mode: currentMode 
                })
            });
            
            const d = await r.json();
            
            if (d.error) {
                msg('❌ ' + d.error, '#f44336');
                sendBtn.disabled = false;
                return;
            }
            
            handleAutomationResponse(d);
            
        } catch (e) {
            console.error('Execute error:', e);
            msg('❌ ' + e.message, '#f44336');
        } finally {
            sendBtn.disabled = false;
        }
        return;
    }
    
    // For assistant mode, use streaming endpoint
    try {
        console.log(`Using SSE streaming for: ${txt}`);
        
        const response = await fetch('/execute_stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                message: txt,
                mode: 'assistant'
            })
        });
        
        if (!response.ok) {
            throw new Error('Streaming request failed');
        }
        
        // Create message container
        const msgDiv = document.createElement('div');
        msgDiv.className = 'message assistant-message';
        
        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        
        // Create terminal container for command output
        const terminalDiv = document.createElement('div');
        terminalDiv.className = 'terminal-output';
        terminalDiv.style.display = 'none'; // Hidden until first command
        
        contentDiv.appendChild(terminalDiv);
        msgDiv.appendChild(contentDiv);
        chatMessages.appendChild(msgDiv);
        
        let hasCommands = false;
        
        // Process SSE stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        
        while (true) {
            const { done, value } = await reader.read();
            
            if (done) break;
            
            buffer += decoder.decode(value, { stream: true });
            
            const events = buffer.split('\n\n');
            buffer = events.pop();
            
            for (const event of events) {
                if (!event.trim() || !event.startsWith('data: ')) continue;
                
                try {
                    const jsonStr = event.substring(6);
                    const data = JSON.parse(jsonStr);
                    
                    if (data.type === 'conversation') {
                        // Conversational response - no terminal
                        contentDiv.removeChild(terminalDiv);
                        contentDiv.textContent = data.content;
                        chatMessages.scrollTop = chatMessages.scrollHeight;
                        
                    } else if (data.type === 'command') {
                        // Show terminal and add command
                        terminalDiv.style.display = 'block';
                        hasCommands = true;
                        
                        const cmdDiv = document.createElement('div');
                        cmdDiv.className = 'command-line';
                        cmdDiv.textContent = '$ ' + data.content;
                        terminalDiv.appendChild(cmdDiv);
                        chatMessages.scrollTop = chatMessages.scrollHeight;
                        
                    } else if (data.type === 'output') {
                        // Add command output
                        const outputDiv = document.createElement('div');
                        outputDiv.className = 'command-output';
                        
                        // Detect special messages
                        if (data.content.includes('[System]') || data.content.includes('Automatically installed')) {
                            outputDiv.className = 'command-output install-message';
                        } else if (data.content.includes('Error') || data.content.includes('❌')) {
                            outputDiv.className = 'command-output error-message';
                        }
                        
                        outputDiv.textContent = data.content;
                        terminalDiv.appendChild(outputDiv);
                        
                        // Add spacing
                        const spacer = document.createElement('div');
                        spacer.style.height = '8px';
                        terminalDiv.appendChild(spacer);
                        
                        chatMessages.scrollTop = chatMessages.scrollHeight;
                        
                    } else if (data.type === 'complete') {
                        // Add success message
                        const completeDiv = document.createElement('div');
                        completeDiv.className = 'command-output success-message';
                        completeDiv.textContent = data.content;
                        terminalDiv.appendChild(completeDiv);
                        chatMessages.scrollTop = chatMessages.scrollHeight;
                        
                    } else if (data.type === 'error' || data.type === 'warning') {
                        // Add error/warning
                        const errorDiv = document.createElement('div');
                        errorDiv.className = data.type === 'error' ? 'command-output error-message' : 'command-output';
                        errorDiv.textContent = data.content;
                        
                        if (hasCommands) {
                            terminalDiv.appendChild(errorDiv);
                        } else {
                            contentDiv.removeChild(terminalDiv);
                            contentDiv.textContent = data.content;
                        }
                        
                        chatMessages.scrollTop = chatMessages.scrollHeight;
                        
                    } else if (data.type === 'done') {
                        // Add timestamp
                        const timestamp = document.createElement('span');
                        timestamp.className = 'timestamp';
                        timestamp.textContent = formatTimestamp(new Date());
                        msgDiv.appendChild(timestamp);
                        
                        console.log('Streaming complete:', data.success);
                    }
                    
                } catch (e) {
                    console.error('Error parsing SSE event:', e);
                }
            }
        }
        
    } catch (e) {
        console.error('Streaming error:', e);
        msg('❌ ' + e.message, '#f44336');
    } finally {
        sendBtn.disabled = false;
    }
}

function handleAssistantResponse(data) {
    const output = data.output || '';
    
    // Check if this is a conversational response (no commands executed)
    const isConversational = !output.includes('$') && !output.includes('Command output:');
    
    if (isConversational) {
        // Display as natural conversation
        addAssistantMessage(output);
    } else {
        // Display as formatted terminal output
        const formattedHTML = formatTerminalOutput(output);
        addAssistantMessage(formattedHTML, true);
    }
}

function handleAutomationResponse(data) {
    // Show task configuration
    task = data.translated_task;
    orig = { ...task };
    abstract.value = task.abstract;
    description.value = task.description;
    verification.value = task.verification;
    
    taskCard.style.display = 'block';
    taskCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
    
    msg('📋 Task configured. Review and execute below.', '#2196f3');
}

function cancelTaskConfig() {
    taskCard.style.display = 'none';
    task = null;
    msg('❌ Task configuration cancelled', '#999');
}

async function executeStructuredTask() {
    task = {
        abstract: abstract.value.trim(),
        description: description.value.trim(),
        verification: verification.value.trim()
    };
    
    if (!task.abstract || !task.description || !task.verification) {
        msg('⚠ All fields are required', '#ff9800');
        return;
    }
    
    executeTaskBtn.disabled = true;
    msg('⏳ Creating structured task...', '#2196f3');
    
    try {
        const r = await fetch('/task', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ translated_task: task })
        });
        const d = await r.json();
        
        if (d.error) {
            msg('❌ ' + d.error, '#f44336');
            executeTaskBtn.disabled = false;
            return;
        }
        
        curTask = d.task_id;
        graphId.value = outputTaskId.value = curTask;
        
        await loadOutputNodes();
        
        // Don't show completion message
        // msg('✅ Task ' + curTask + ' started', '#4caf50');
        taskCard.style.display = 'none';
        
        // Switch to output tab
        switchTab('output');
        startAuto();
        loadTasks();
        
    } catch (e) {
        console.error('Task creation error:', e);
        msg('❌ ' + e.message, '#f44336');
    } finally {
        executeTaskBtn.disabled = false;
    }
}

function handleReset() {
    Object.values(intervals).forEach(i => i && clearInterval(i));
    task = orig = null;
    curTask = null;
    nodeCache = {};
    input.value = '';
    abstract.value = '';
    description.value = '';
    verification.value = '';
    taskCard.style.display = 'none';
    outputNodeSelect.innerHTML = '<option value="">Select task first...</option>';
    sendBtn.disabled = false;
    
    // Clear chat messages except welcome
    const welcome = chatMessages.querySelector('.welcome-message');
    chatMessages.innerHTML = '';
    if (welcome) {
        chatMessages.appendChild(welcome);
    }
    
    msg('🔄 Reset complete', '#999');
    fetch('/reset', { method: 'POST' });
}

// =================================================================
// Task List & Node Management
// =================================================================

async function loadTasks() {
    try {
        const r = await fetch('/task/status');
        const d = await r.json();
        if (d.error) return taskList.innerHTML = '<div style="color:#f44336;padding:20px">' + d.error + '</div>';
        
        const roots = d.tasks.filter(t => t.type === 'root');
        const nodes = d.tasks.filter(t => t.type === 'node');
        count.textContent = roots.length + ' task(s), ' + nodes.length + ' node(s)';
        
        if (!roots.length) return taskList.innerHTML = '<div style="color:#999;padding:40px;text-align:center">No tasks yet</div>';
        
        taskList.innerHTML = '';
        
        roots.forEach(t => {
            const div = document.createElement('div');
            div.className = 'task ' + t.status;
            
            const canImprove = ['failed', 'cancelled', 'impossible', 'completed'].includes(t.status);
            const canCancel = ['working', 'planning', 'pending'].includes(t.status);
            
            div.innerHTML = `
                <div class="task-header">
                    <span style="font-family:monospace">🎯 ${t.task_id}</span>
                    <span class="badge">${t.status}</span>
                </div>
                <div style="font-weight:600;margin-bottom:8px">${t.abstract}</div>
                <div class="controls">
                    <button class="btn-primary" onclick="viewTask('${t.task_id}',event)">👁 View</button>
                    <button class="btn-warning" onclick="cancelTask('${t.task_id}',event)" ${canCancel ? '' : 'disabled'}>⏸ Cancel</button>
                    <button class="btn-success" onclick="completeTask('${t.task_id}',event)">✅ Complete</button>
                    <button class="btn-info" onclick="showTaskImprove('${t.task_id}',event)" ${canImprove ? '' : 'disabled'}>💡 Improve</button>
                </div>
            `;
            taskList.appendChild(div);
            
            nodes.filter(n => n.task_id === t.task_id).forEach(n => {
                const nd = document.createElement('div');
                nd.className = 'task node ' + n.status;
                
                const canStart = ['pending', 'cancelled', 'failed'].includes(n.status);
                const canImprove = ['failed', 'cancelled', 'impossible'].includes(n.status);
                const canCancel = ['working', 'planning', 'pending'].includes(n.status);
                
                nd.innerHTML = `
                    <div class="task-header">
                        <span style="font-family:monospace">📦 ${n.node_id}</span>
                        <span class="badge">${n.status}</span>
                    </div>
                    <div style="margin-bottom:8px">${n.abstract}</div>
                    <div class="controls">
                        <button class="btn-primary" onclick="viewNode('${n.node_id}',event)">👁 View</button>
                        <button class="btn-warning" onclick="cancelNode('${n.node_id}',event)" ${canCancel ? '' : 'disabled'}>⏸ Cancel</button>
                        <button class="btn-success" onclick="completeNode('${n.node_id}',event)">✅ Complete</button>
                        <button class="btn-info" onclick="forceStartNode('${n.node_id}',event)" ${canStart ? '' : 'disabled'}>▶️ Start</button>
                        <button class="btn-info" onclick="showNodeImprove('${n.node_id}',event)" ${canImprove ? '' : 'disabled'}>💡 Improve</button>
                        <button class="btn-danger" onclick="removeNode('${n.node_id}',event)">🗑 Remove</button>
                    </div>
                `;
                taskList.appendChild(nd);
            });
        });
        
        if (tasksViewSelect.value === 'graph') {
            loadTasksGraph();
        }
    } catch (e) {
        taskList.innerHTML = '<div style="color:#f44336">Error: ' + e.message + '</div>';
    }
}

async function loadTasksGraph() {
    try {
        const r = await fetch('/task/status');
        const d = await r.json();
        if (d.error) {
            taskGraphView.innerHTML = '<div style="color:#f44336;padding:20px">' + d.error + '</div>';
            return;
        }
        
        const roots = d.tasks.filter(t => t.type === 'root');
        if (!roots.length) {
            taskGraphView.innerHTML = '<div style="color:#999;padding:40px;text-align:center">No tasks yet</div>';
            return;
        }
        
        graphScrollPos.tasks = taskGraph.scrollTop;
        
        let combinedGraph = 'graph TD\n';
        let styles = new Set();
        
        for (const root of roots) {
            const graphR = await fetch('/tree?task_id=' + root.task_id);
            const graphD = await graphR.json();
            if (graphD.graph) {
                const lines = graphD.graph.split('\n');
                lines.forEach(line => {
                    const trimmed = line.trim();
                    if (trimmed && !trimmed.startsWith('graph ') && !trimmed.startsWith('%%') && 
                        !trimmed.startsWith('classDef') && !trimmed.startsWith('class ')) {
                        combinedGraph += '    ' + trimmed + '\n';
                    } else if (trimmed.startsWith('classDef') || trimmed.startsWith('class ')) {
                        styles.add(trimmed);
                    }
                });
            }
        }
        
        combinedGraph += '\n    %% Enhanced styling for dark mode\n';
        combinedGraph += '    classDef completed fill:#2e7d32,stroke:#4caf50,stroke-width:3px,color:#ffffff\n';
        combinedGraph += '    classDef working fill:#f57c00,stroke:#ff9800,stroke-width:3px,color:#ffffff\n';
        combinedGraph += '    classDef planning fill:#1976d2,stroke:#2196f3,stroke-width:3px,color:#ffffff\n';
        combinedGraph += '    classDef failed fill:#c62828,stroke:#f44336,stroke-width:3px,color:#ffffff\n';
        combinedGraph += '    classDef cancelled fill:#616161,stroke:#9e9e9e,stroke-width:3px,color:#ffffff\n';
        combinedGraph += '    classDef impossible fill:#6a1b9a,stroke:#9c27b0,stroke-width:3px,color:#ffffff\n';
        combinedGraph += '    classDef pending fill:#37474f,stroke:#607d8b,stroke-width:2px,color:#e0e0e0\n';
        
        styles.forEach(style => {
            combinedGraph += '    ' + style + '\n';
        });
        
        let themedGraph = `%%{init: {'theme':'dark', 'themeVariables': {
            'primaryColor': '#2d2d2d',
            'primaryTextColor': '#e0e0e0',
            'primaryBorderColor': '#64b5f6',
            'lineColor': '#64b5f6',
            'secondaryColor': '#1e3a5f',
            'tertiaryColor': '#3d2f1f',
            'background': '#1a1a1a',
            'mainBkg': '#2d2d2d',
            'secondBkg': '#3d3d3d',
            'mainContrastColor': '#e0e0e0',
            'darkMode': true,
            'fontFamily': 'Arial',
            'fontSize': '14px'
        }}}%%\n` + combinedGraph;
        
        taskGraphView.innerHTML = '<div class="mermaid">' + themedGraph + '</div>';
        mermaid.init(undefined, taskGraphView.querySelector('.mermaid'));
        
        setTimeout(() => {
            taskGraph.scrollTop = graphScrollPos.tasks;
        }, 100);
        
    } catch (e) {
        taskGraphView.innerHTML = '<div style="color:#f44336">' + e.message + '</div>';
    }
}

// =================================================================
// Node Dropdown Management
// =================================================================

async function loadOutputNodes() {
    const taskId = outputTaskId.value.trim();
    if (!taskId) {
        outputNodeSelect.innerHTML = '<option value="">Select task first...</option>';
        return;
    }
    
    const currentSelection = outputNodeSelect.value;
    const hadSelection = currentSelection && currentSelection !== '';
    
    try {
        const r = await fetch('/task/' + taskId + '/nodes');
        const d = await r.json();
        
        if (d.error) {
            outputNodeSelect.innerHTML = '<option value="">Error loading nodes</option>';
            return;
        }
        
        nodeCache[taskId] = d.nodes;
        populateNodeDropdown(outputNodeSelect, d.nodes);
        
        if (hadSelection) {
            const optionExists = Array.from(outputNodeSelect.options).some(opt => opt.value === currentSelection);
            if (optionExists) {
                outputNodeSelect.value = currentSelection;
            }
        } else if (d.nodes.length > 0) {
            outputNodeSelect.value = d.nodes[0].node_id;
            await loadOutput();
        }
        
    } catch (e) {
        console.error('Error loading nodes:', e);
        outputNodeSelect.innerHTML = '<option value="">Error loading nodes</option>';
    }
}

function populateNodeDropdown(selectEl, nodes) {
    selectEl.innerHTML = '';
    
    if (!nodes || nodes.length === 0) {
        selectEl.innerHTML = '<option value="">No nodes yet</option>';
        return;
    }
    
    nodes.forEach(node => {
        const opt = document.createElement('option');
        opt.value = node.node_id;
        
        const indent = '　'.repeat(node.depth);
        
        const statusEmoji = {
            'pending': '⏳',
            'planning': '🧠',
            'working': '⚙️',
            'completed': '✅',
            'failed': '❌',
            'cancelled': '🚫',
            'impossible': '⛔'
        }[node.status] || '◯';
        
        const abstractShort = node.abstract.length > 50 
            ? node.abstract.substring(0, 50) + '...' 
            : node.abstract;
        
        opt.textContent = `${indent}${statusEmoji} ${abstractShort}`;
        opt.className = `depth-${node.depth}`;
        
        selectEl.appendChild(opt);
    });
}

// =================================================================
// Task Actions (used by task list)
// =================================================================

async function viewTask(id, e) {
    e.stopPropagation();
    graphId.value = outputTaskId.value = id;
    await loadOutputNodes();
    await loadGraph();
    switchTab('graph');
}

async function viewNode(id, e) {
    e.stopPropagation();
    try {
        const r = await fetch('/node/' + id);
        const d = await r.json();
        alert('Node: ' + id + '\nStatus: ' + d.status + '\nAbstract: ' + d.abstract + 
              '\n\nTerminal Outputs: ' + d.terminal_output.length + 
              '\nLLM Responses: ' + d.llm_responses.length);
    } catch (e) {
        msg('❌ ' + e.message, '#f44336');
    }
}

async function cancelTask(id, e) {
    e.stopPropagation();
    if (!confirm('Cancel task ' + id + ' and all its nodes?')) return;
    try {
        const r = await fetch('/task/' + id + '/cancel', { method: 'PUT' });
        const d = await r.json();
        msg(d.error ? '❌ ' + d.error : '✅ Cancelled ' + id, d.error ? '#f44336' : '#4caf50');
        loadTasks();
    } catch (e) {
        msg('❌ ' + e.message, '#f44336');
    }
}

async function cancelNode(id, e) {
    e.stopPropagation();
    if (!confirm('Cancel node ' + id + '?')) return;
    try {
        const r = await fetch('/node/' + id + '/cancel', { method: 'PUT' });
        const d = await r.json();
        msg(d.error ? '❌ ' + d.error : '✅ Cancelled ' + id, d.error ? '#f44336' : '#4caf50');
        loadTasks();
    } catch (e) {
        msg('❌ ' + e.message, '#f44336');
    }
}

async function completeTask(id, e) {
    e.stopPropagation();
    if (!confirm('Mark task ' + id + ' as completed?')) return;
    try {
        const r = await fetch('/task/' + id + '/complete', { method: 'PUT' });
        const d = await r.json();
        msg(d.error ? '❌ ' + d.error : '✅ Completed ' + id, d.error ? '#f44336' : '#4caf50');
        loadTasks();
    } catch (e) {
        msg('❌ ' + e.message, '#f44336');
    }
}

async function completeNode(id, e) {
    e.stopPropagation();
    if (!confirm('Mark node ' + id + ' as completed?')) return;
    try {
        const r = await fetch('/node/' + id + '/complete', { method: 'PUT' });
        const d = await r.json();
        msg(d.error ? '❌ ' + d.error : '✅ Completed ' + id, d.error ? '#f44336' : '#4caf50');
        loadTasks();
        loadGraph();
    } catch (e) {
        msg('❌ ' + e.message, '#f44336');
    }
}

async function forceStartNode(id, e) {
    e.stopPropagation();
    if (!confirm('Force start node ' + id + '?')) return;
    try {
        const r = await fetch('/node/' + id + '/start', { method: 'POST' });
        const d = await r.json();
        msg(d.error ? '❌ ' + d.error : '✅ Started ' + id, d.error ? '#f44336' : '#4caf50');
        loadTasks();
    } catch (e) {
        msg('❌ ' + e.message, '#f44336');
    }
}

async function removeNode(id, e) {
    e.stopPropagation();
    if (!confirm('Remove node ' + id + ' and its subtree?')) return;
    try {
        const r = await fetch('/node/' + id + '/remove', { method: 'DELETE' });
        const d = await r.json();
        msg(d.error ? '❌ ' + d.error : '✅ Removed ' + id, d.error ? '#f44336' : '#4caf50');
        loadTasks();
    } catch (e) {
        msg('❌ ' + e.message, '#f44336');
    }
}

// =================================================================
// Task/Node Improvement
// =================================================================

function showTaskImprove(id, e) {
    e.stopPropagation();
    improveTaskId = id;
    improveTaskComments.value = '';
    improveTaskModal.classList.add('active');
}

function showNodeImprove(id, e) {
    e.stopPropagation();
    improveNodeId = id;
    improveNodeComments.value = '';
    improveNodeModal.classList.add('active');
}

async function submitTaskImprovement() {
    if (!improveTaskId) return;
    
    const comments = improveTaskComments.value.trim();
    
    try {
        const r = await fetch('/task/' + improveTaskId + '/restart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ comments: comments })
        });
        const d = await r.json();
        
        if (d.error) {
            msg('❌ ' + d.error, '#f44336');
        } else {
            // Don't show completion message
            // msg('✅ Restarted as ' + d.new_task_id + (comments ? ' with improvements' : ''), '#4caf50');
            curTask = d.new_task_id;
            graphId.value = outputTaskId.value = d.new_task_id;
            await loadOutputNodes();
            switchTab('output');
            startAuto();
        }
        
        improveTaskModal.classList.remove('active');
        improveTaskId = null;
        loadTasks();
        
    } catch (e) {
        msg('❌ ' + e.message, '#f44336');
    }
}

async function submitNodeImprovement() {
    if (!improveNodeId) return;
    
    const comments = improveNodeComments.value.trim();
    
    try {
        const r = await fetch('/node/' + improveNodeId + '/restart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ comments: comments })
        });
        const d = await r.json();
        
        if (d.error) {
            msg('❌ ' + d.error, '#f44336');
        } else {
            // Don't show completion message
            // msg('✅ Node restarted as ' + d.new_node_id + (comments ? ' with improvements' : ''), '#4caf50');
        }
        
        improveNodeModal.classList.remove('active');
        improveNodeId = null;
        loadTasks();
        loadGraph();
        
    } catch (e) {
        msg('❌ ' + e.message, '#f44336');
    }
}

// =================================================================
// Graph Visualization
// =================================================================

async function loadGraph() {
    const id = graphId.value.trim();
    if (!id) return msg('⚠ Enter task ID', '#ff9800');
    
    graphScrollPos.graph = graphView.scrollTop;
    
    try {
        const r = await fetch('/tree?task_id=' + id);
        const d = await r.json();
        if (d.error) return graphView.innerHTML = '<div style="color:#f44336;padding:20px">' + d.error + '</div>';
        
        let graph = d.graph;
        if (!graph.includes('%%{init:')) {
            graph = `%%{init: {'theme':'dark', 'themeVariables': {
                'primaryColor': '#2d2d2d',
                'primaryTextColor': '#e0e0e0',
                'primaryBorderColor': '#64b5f6',
                'lineColor': '#64b5f6',
                'secondaryColor': '#1e3a5f',
                'tertiaryColor': '#3d2f1f',
                'background': '#1a1a1a',
                'mainBkg': '#2d2d2d',
                'secondBkg': '#3d3d3d',
                'mainContrastColor': '#e0e0e0',
                'darkMode': true,
                'fontFamily': 'Arial',
                'fontSize': '14px'
            }}}%%\n` + graph;
        }
        
        graphView.innerHTML = '<div class="mermaid">' + graph + '</div>';
        mermaid.init(undefined, graphView.querySelector('.mermaid'));
        
        setTimeout(() => {
            graphView.scrollTop = graphScrollPos.graph;
        }, 100);
        
    } catch (e) {
        graphView.innerHTML = '<div style="color:#f44336">' + e.message + '</div>';
    }
}

// =================================================================
// Output Viewer
// =================================================================

async function loadOutput() {
    const nodeId = outputNodeSelect.value;
    if (!nodeId) return;
    
    try {
        const r = await fetch('/node/' + nodeId + '/log');
        const d = await r.json();
        
        if (d.error) {
            outputView.textContent = d.error;
            return;
        }
        
        outputView.textContent = d.log;
        outputView.scrollTop = outputView.scrollHeight;
        
    } catch (e) {
        outputView.textContent = 'Error: ' + e.message;
    }
}

// =================================================================
// Auto-Refresh
// =================================================================

function toggleAuto(type) {
    auto[type] = !auto[type];
    const btn = type === 'graph' ? graphAuto : outputAuto;
    btn.textContent = 'Auto: ' + (auto[type] ? 'ON' : 'OFF');
    btn.className = auto[type] ? 'btn-success' : 'btn-primary';
    
    if (auto[type]) {
        if (type === 'graph') {
            intervals.graph = setInterval(loadGraph, 3000);
        } else if (type === 'output') {
            intervals.output = setInterval(loadOutput, 3000);
            intervals.outputNodes = setInterval(loadOutputNodes, 5000);
        }
    } else {
        if (type === 'graph' && intervals.graph) {
            clearInterval(intervals.graph);
            intervals.graph = null;
        } else if (type === 'output') {
            if (intervals.output) {
                clearInterval(intervals.output);
                intervals.output = null;
            }
            if (intervals.outputNodes) {
                clearInterval(intervals.outputNodes);
                intervals.outputNodes = null;
            }
        }
    }
}

function startAuto() {
    auto.graph = auto.output = true;
    
    graphAuto.textContent = 'Auto: ON';
    graphAuto.className = 'btn-success';
    intervals.graph = setInterval(loadGraph, 3000);
    
    outputAuto.textContent = 'Auto: ON';
    outputAuto.className = 'btn-success';
    intervals.output = setInterval(loadOutput, 3000);
    intervals.outputNodes = setInterval(loadOutputNodes, 5000);
    
    intervals.poll = setInterval(async () => {
        try {
            const r = await fetch('/task/' + curTask);
            const d = await r.json();
            if (['completed', 'failed', 'cancelled', 'impossible'].includes(d.status)) {
                Object.values(intervals).forEach(i => i && clearInterval(i));
                loadOutput();
                loadGraph();
                loadTasks();
                // Don't show completion message
                // msg('✅ Task ' + d.status, d.status === 'completed' ? '#4caf50' : '#f44336');
            }
        } catch (e) {}
    }, 3000);
}

// =================================================================
// File Browser
// =================================================================

async function loadFiles(p) {
    curPath = p;
    path.textContent = p;
    upBtn.style.display = (p !== '/app/work' && p !== '/app' && p !== '/') ? 'block' : 'none';
    fileView.style.display = 'none';
    
    try {
        const r = await fetch('/files?path=' + encodeURIComponent(p));
        const d = await r.json();
        if (d.error) return fileGrid.innerHTML = '<div style="grid-column:1/-1;color:#f44336">' + d.error + '</div>';
        if (!d.files.length) return fileGrid.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:#999;padding:40px">Empty</div>';
        
        fileGrid.innerHTML = '';
        d.files.sort((a, b) => 
            a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'directory' ? -1 : 1
        ).forEach(f => {
            const c = document.createElement('div');
            c.className = 'file-card ' + (f.type === 'directory' ? 'dir' : 'file');
            const icon = f.type === 'directory' ? '📁' : 
                        f.name.endsWith('.txt') ? '📄' :
                        f.name.endsWith('.log') ? '📋' :
                        f.name.endsWith('.json') ? '📊' : '📄';
            c.innerHTML = '<div class="icon">' + icon + '</div>' +
                         '<div style="font-weight:600;font-size:13px;word-break:break-word">' + f.name + '</div>' +
                         '<div style="font-size:11px;color:#999;margin-top:5px">' + f.size + '</div>';
            c.onclick = () => f.type === 'directory' ? loadFiles(f.full_path) : viewFile(f.full_path);
            fileGrid.appendChild(c);
        });
    } catch (e) {
        fileGrid.innerHTML = '<div style="grid-column:1/-1;color:#f44336">' + e.message + '</div>';
    }
}

function goUp() {
    loadFiles(curPath.split('/').slice(0, -1).join('/') || '/');
}

async function viewFile(p) {
    fileView.style.display = 'block';
    fileView.innerHTML = '<div style="text-align:center;color:#999">Loading...</div>';
    try {
        const r = await fetch('/file?path=' + encodeURIComponent(p));
        const d = await r.json();
        if (d.error) return fileView.innerHTML = '<div style="color:#f44336">' + d.error + '</div>';
        const name = p.split('/').pop();
        fileView.innerHTML = '<h3>📄 ' + name + '</h3>' +
                            '<button class="btn-primary" onclick="copyFile()">📋 Copy</button> ' +
                            '<button class="btn-danger" onclick="fileView.style.display=\'none\'">✖ Close</button>' +
                            '<pre id="fc">' + esc(d.content) + '</pre>';
    } catch (e) {
        fileView.innerHTML = '<div style="color:#f44336">' + e.message + '</div>';
    }
}

function copyFile() {
    const fc = document.getElementById('fc');
    navigator.clipboard.writeText(fc.textContent)
        .then(() => msg('📋 Copied!', '#4caf50'))
        .catch(() => msg('❌ Copy failed', '#f44336'));
}