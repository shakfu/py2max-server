/**
 * py2max Interactive Editor - Client-side JavaScript
 * Uses WebSocket for bidirectional real-time communication
 */

// Common Max/MSP objects offered as typeahead suggestions in the create modal.
// Not exhaustive -- users can type any object name.
const COMMON_MAX_OBJECTS = [
    // Control / messaging
    'message', 'comment', 'bang', 'toggle', 'button', 'metro', 'counter',
    'int', 'float', 'number', 'flonum', 'trigger', 't', 'gate', 'switch',
    'select', 'sel', 'route', 'pack', 'unpack', 'pak', 'zl', 'coll', 'dict',
    'send', 'receive', 's', 'r', 'loadbang', 'delay', 'pipe', 'line',
    'scale', 'expr', 'if', 'change', 'past', 'peak', 'trough', 'accum',
    // MSP (signal)
    'cycle~', 'phasor~', 'saw~', 'rect~', 'tri~', 'noise~', 'pink~',
    'gain~', 'live.gain~', '*~', '+~', '-~', '/~', 'sig~', 'line~', 'curve~',
    'adsr~', 'dac~', 'adc~', 'ezdac~', 'ezadc~', 'meter~', 'scope~',
    'lores~', 'onepole~', 'biquad~', 'filtergraph~', 'svf~', 'reson~',
    'delay~', 'tapin~', 'tapout~', 'record~', 'groove~', 'buffer~', 'play~',
    'selector~', 'gate~', 'matrix~', 'send~', 'receive~', 'pfft~', 'fft~',
    // UI
    'slider', 'dial', 'live.dial', 'live.slider', 'kslider', 'pictslider',
    'umenu', 'tab', 'matrixctrl', 'nslider', 'rslider', 'multislider',
    'function', 'waveform~', 'spectroscope~', 'live.text', 'live.button',
    // Structure
    'patcher', 'p', 'bpatcher', 'poly~', 'inlet', 'outlet', 'in', 'out',
];

class InteractiveEditor {
    constructor() {
        this.boxes = new Map();
        this.lines = [];
        this.ws = null;
        this.svgNS = 'http://www.w3.org/2000/svg';

        // Interaction state
        this.selectedBox = null;
        this.selectedBoxes = new Set();  // Multi-select: ids of selected boxes
        this.selectedLine = null;  // Track selected patchline for deletion
        this.dragging = false;
        this.dragStarted = false;  // Track if drag actually started
        this.dragOffset = { x: 0, y: 0 };
        this.mouseDownPos = null;  // Track mouse down position
        this.dragBoxes = null;     // Per-box start positions for a (group) drag
        this.dragAnchorId = null;  // The box actually pressed (collapse target)
        this.marquee = null;       // Active rubber-band selection, if any

        // Connection state - tracks outlet -> inlet connections
        this.connectionStart = null;  // {box, portIndex, isOutlet} (two-click fallback)
        this.connectionDrag = null;   // {box, portIndex, isOutlet, start, moved} (drag-to-connect)
        this.previewLine = null;      // transient rubber-band cord during a drag

        // Pan/zoom state. When userAdjustedView is true, updateViewBox() leaves
        // the viewBox alone (preserving the user's zoom/pan) instead of
        // auto-fitting on every render. It re-engages auto-fit when the shown
        // patcher changes (open file / navigate), tracked by _viewPatcherKey.
        this.userAdjustedView = false;
        this._viewPatcherKey = null;
        this.panning = false;
        this.panStart = null;   // {clientX, clientY, vb} captured at pan start
        this.spaceDown = false; // Space held -> left-drag pans instead of selecting

        // Store original box positions for reset functionality
        this.originalPositions = new Map();

        // Current filepath (for save/save-as logic)
        this.currentFilepath = null;

        // Reconnect backoff state (see initializeWebSocket / onclose)
        this.reconnectAttempts = 0;
        this.reconnectTimer = null;
        this.MAX_RECONNECT_ATTEMPTS = 8;  // ~ up to 30 s cap, then give up

        // Whether to hide comment annotation nodes (they clutter auto-layout).
        // Non-destructive: comments stay in the patch and are still saved.
        this.hideComments = localStorage.getItem('hideComments') === 'true';

        this.initializeWebSocket();
        this.initializeSVG();
        this.initializeControls();
        this.initializeCreateModal();
        this.initializeHeaderHeightSync();
    }

    initializeHeaderHeightSync() {
        /**
         * Keep the --header-height CSS variable in sync with the header's actual
         * rendered height. The header wraps to multiple lines at narrow widths,
         * so a fixed height would let the fixed-position sidebar overlap it (and
         * mis-size the canvas). ResizeObserver re-measures on any header reflow.
         */
        const header = document.getElementById('header');
        if (!header) return;

        const sync = () => {
            const height = header.offsetHeight;
            if (height > 0) {
                document.documentElement.style.setProperty(
                    '--header-height', `${height}px`
                );
            }
        };

        sync();

        if (typeof ResizeObserver !== 'undefined') {
            this._headerResizeObserver = new ResizeObserver(sync);
            this._headerResizeObserver.observe(header);
        }
        window.addEventListener('resize', sync);
    }

    initializeWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        // WebSocket runs on port + 1
        const wsPort = parseInt(window.location.port) + 1;
        const wsUrl = `${protocol}//${window.location.hostname}:${wsPort}/ws`;

        this.ws = new WebSocket(wsUrl);
        this.authenticated = false;

        this.ws.onopen = () => {
            this.updateStatus('Authenticating...', 'disconnected');
            console.log('WebSocket connection opened, sending authentication...');

            // Send authentication token
            const token = window.PY2MAX_SESSION_TOKEN || '';
            if (!token) {
                console.error('No session token found');
                this.updateStatus('No Auth Token', 'disconnected');
                this.ws.close();
                return;
            }

            this.ws.send(JSON.stringify({
                type: 'auth',
                token: token
            }));
        };

        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);

                // Handle authentication response
                if (data.type === 'auth_success') {
                    this.authenticated = true;
                    // A confirmed connection resets the backoff so the next drop
                    // starts probing quickly again.
                    this.reconnectAttempts = 0;
                    this.updateStatus('Connected', 'connected');
                    console.log('Authentication successful');
                    return;
                }

                if (data.type === 'error') {
                    this.updateStatus(`Error: ${data.message}`, 'disconnected');
                    console.error('Server error:', data.message);
                    this.ws.close();
                    return;
                }

                // Only process other messages if authenticated
                if (this.authenticated) {
                    this.handleUpdate(data);
                }
            } catch (e) {
                console.error('Failed to parse WebSocket message:', e);
                this.updateStatus('Parse Error', 'disconnected');
            }
        };

        this.ws.onerror = (error) => {
            this.updateStatus('Error', 'disconnected');
            console.error('WebSocket error:', error);
        };

        this.ws.onclose = (event) => {
            this.authenticated = false;

            if (event.code === 1008) {
                // Authentication failure - don't reconnect
                this.updateStatus('Authentication Failed', 'disconnected');
                console.error('Authentication failed, not reconnecting');
                return;
            }

            console.log('WebSocket connection closed');

            // Give up after a bounded number of tries instead of spinning on a
            // dead server forever.
            if (this.reconnectAttempts >= this.MAX_RECONNECT_ATTEMPTS) {
                this.updateStatus('Disconnected - reload to retry', 'disconnected');
                console.error('Giving up reconnecting after', this.reconnectAttempts, 'attempts');
                return;
            }

            // Exponential backoff with a 30 s cap: 1s, 2s, 4s, 8s, 16s, 30s...
            const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
            this.reconnectAttempts += 1;
            this.updateStatus(`Disconnected - retrying in ${Math.round(delay / 1000)}s`, 'disconnected');

            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = setTimeout(() => {
                this.updateStatus('Reconnecting...', 'disconnected');
                this.initializeWebSocket();
            }, delay);
        };
    }

    initializeSVG() {
        const canvas = document.getElementById('canvas');

        // Create SVG using SVG.js library
        this.draw = SVG().addTo('#canvas').size('100%', '100%');
        this.draw.viewbox(0, 0, 1200, 800);
        this.draw.attr('preserveAspectRatio', 'xMidYMid meet');

        // Get the native SVG element for event listeners
        this.svg = this.draw.node;

        // Create groups for layers using SVG.js
        this.linesGroupSVG = this.draw.group().id('patchlines');
        this.boxesGroupSVG = this.draw.group().id('boxes');

        // Get native DOM elements for compatibility with existing code
        this.linesGroup = this.linesGroupSVG.node;
        this.boxesGroup = this.boxesGroupSVG.node;

        // Add event listeners for canvas interactions
        this.svg.addEventListener('mousedown', this.handleCanvasMouseDown.bind(this));
        this.svg.addEventListener('mousemove', this.handleCanvasMouseMove.bind(this));
        this.svg.addEventListener('mouseup', this.handleCanvasMouseUp.bind(this));
        // Double-click on empty canvas creates an object. Box double-clicks
        // (open subpatcher / edit text) are detected in handleBoxMouseDown,
        // because re-rendering on mousedown makes the native dblclick unreliable.
        this.svg.addEventListener('dblclick', this.handleCanvasDoubleClick.bind(this));

        // Wheel zooms toward the cursor. passive:false so preventDefault stops
        // the page from scrolling under the zoom.
        this.svg.addEventListener('wheel', this.handleWheel.bind(this), { passive: false });
        // End an in-progress pan if the pointer leaves the canvas.
        this.svg.addEventListener('mouseleave', () => this.endPan());

        console.log('SVG.js initialized:', SVG);
    }

    initializeControls() {
        // Load layout mode preference from localStorage (default to sidebar)
        const savedMode = localStorage.getItem('layoutControlsMode') || 'sidebar';
        if (savedMode === 'sidebar') {
            document.body.classList.add('sidebar-mode');
        }

        // Toggle layout mode button - switch between panel and sidebar
        const toggleLayoutModeBtn = document.getElementById('toggle-layout-mode-btn');
        if (toggleLayoutModeBtn) {
            // Update button text based on current mode
            this.updateLayoutModeButton();

            toggleLayoutModeBtn.addEventListener('click', () => {
                document.body.classList.toggle('sidebar-mode');
                this.updateLayoutModeButton();

                // Save preference
                const mode = document.body.classList.contains('sidebar-mode') ? 'sidebar' : 'panel';
                localStorage.setItem('layoutControlsMode', mode);
            });
        }

        // Open uses the server-side file picker (lists .maxpat files on the
        // server): it avoids the browser file API entirely, which fixes Open in
        // Safari and preserves the real filesystem path for save-back.
        const openBtn = document.getElementById('open-btn');
        if (openBtn) {
            openBtn.addEventListener('click', () => this.openFilePicker());
        }

        // Native upload is kept as a fallback (e.g. server and browser on
        // different machines). The <label for="file-input"> in the picker modal
        // opens the dialog without a programmatic .click() (which Safari blocks).
        const fileInput = document.getElementById('file-input');
        if (fileInput) {
            fileInput.addEventListener('change', (e) => this.handleFileSelected(e));
        }

        // File-picker modal controls
        const pickerClose = document.getElementById('file-picker-close');
        if (pickerClose) {
            pickerClose.addEventListener('click', () => this.closeFilePicker());
        }
        const pickerBackdrop = document.getElementById('file-picker-backdrop');
        if (pickerBackdrop) {
            pickerBackdrop.addEventListener('click', () => this.closeFilePicker());
        }

        // Save button
        const saveBtn = document.getElementById('save-btn');
        if (saveBtn) {
            saveBtn.addEventListener('click', () => {
                this.handleSave();
            });
        }

        // Create object button
        const createBtn = document.getElementById('create-btn');
        if (createBtn) {
            createBtn.addEventListener('click', () => {
                this.createObjectDialog();
            });
        }

        // Hide/Show comments toggle
        const toggleCommentsBtn = document.getElementById('toggle-comments-btn');
        if (toggleCommentsBtn) {
            this.updateCommentsButton();
            toggleCommentsBtn.addEventListener('click', () => {
                this.hideComments = !this.hideComments;
                localStorage.setItem('hideComments', String(this.hideComments));
                this.updateCommentsButton();
                this.render();
            });
        }

        // Parent button - navigate to parent patcher
        const parentBtn = document.getElementById('parent-btn');
        if (parentBtn) {
            parentBtn.addEventListener('click', () => {
                this.navigateToParent();
            });
        }

        // Fit-to-view button (re-engage auto-fit after zoom/pan).
        const fitViewBtn = document.getElementById('fit-view-btn');
        if (fitViewBtn) {
            fitViewBtn.addEventListener('click', () => this.fitView());
        }

        // Undo / redo buttons
        const undoBtn = document.getElementById('undo-btn');
        if (undoBtn) {
            undoBtn.addEventListener('click', () => this.undo());
        }
        const redoBtn = document.getElementById('redo-btn');
        if (redoBtn) {
            redoBtn.addEventListener('click', () => this.redo());
        }

        // Auto-layout button - toggle layout controls panel
        const autoLayoutBtn = document.getElementById('auto-layout-btn');
        if (autoLayoutBtn) {
            autoLayoutBtn.addEventListener('click', () => {
                const controlsPanel = document.getElementById('layout-controls');
                const isSidebarMode = document.body.classList.contains('sidebar-mode');

                controlsPanel.classList.toggle('visible');

                // In sidebar mode, also toggle the controls-visible class on body for canvas padding
                if (isSidebarMode) {
                    document.body.classList.toggle('controls-visible');
                }

                // If panel just became visible, apply layout immediately
                if (controlsPanel.classList.contains('visible')) {
                    this.autoLayout();
                }
            });
        }

        // Initialize layout controls
        this.initializeLayoutControls();

        // Keyboard handler for delete/backspace and ESC
        document.addEventListener('keydown', (e) => {
            // While a modal is open, Escape closes it and other editor keys are
            // ignored so they don't act on the patch behind the modal. (The
            // create modal's own input also handles Enter/Escape and stops
            // propagation, so this mainly covers Escape when it isn't focused.)
            if (this.isFilePickerOpen()) {
                if (e.key === 'Escape') {
                    e.preventDefault();
                    this.closeFilePicker();
                }
                return;
            }
            if (this.isCreateModalOpen()) {
                if (e.key === 'Escape') {
                    e.preventDefault();
                    this.closeCreateModal();
                }
                return;
            }

            // Don't hijack keys while a form control is focused (layout
            // <select>/sliders, or the inline text-edit <input>): Backspace there
            // must edit the field, not delete the selected box, and Escape must
            // not navigate away.
            const t = e.target;
            const inFormControl = !!t && (
                t.tagName === 'INPUT' ||
                t.tagName === 'TEXTAREA' ||
                t.tagName === 'SELECT' ||
                t.isContentEditable
            );
            if (inFormControl) return;

            // Undo/redo. Gated below the form-control check above, so Cmd/Ctrl-Z
            // in the inline text editor undoes typing (browser default), not the
            // patch.
            if ((e.metaKey || e.ctrlKey) && (e.key === 'z' || e.key === 'Z')) {
                e.preventDefault();
                if (e.shiftKey) this.redo(); else this.undo();
                return;
            }
            if ((e.metaKey || e.ctrlKey) && (e.key === 'y' || e.key === 'Y')) {
                e.preventDefault();
                this.redo();
                return;
            }

            if (e.key === 'Delete' || e.key === 'Backspace') {
                // Only intercept (and suppress browser back-navigation) when
                // there is actually something selected to delete.
                if (this.selectedBox || this.selectedLine || (this.selectedBoxes && this.selectedBoxes.size)) {
                    e.preventDefault();
                    this.handleDelete();
                }
            } else if (e.key === 'Escape') {
                // ESC key navigates to parent patcher
                this.navigateToParent();
            } else if (e.key === 'f' || e.key === 'F') {
                // Fit the whole patch to the view (re-engage auto-fit).
                e.preventDefault();
                this.fitView();
            } else if (e.key === ' ') {
                // Hold Space to pan with a left-drag; suppress page scroll.
                e.preventDefault();
                if (!this.spaceDown) {
                    this.spaceDown = true;
                    if (!this.panning) this.svg.style.cursor = 'grab';
                }
            }
        });

        document.addEventListener('keyup', (e) => {
            if (e.key === ' ') {
                this.spaceDown = false;
                if (!this.panning) this.svg.style.cursor = '';
            }
        });

        // Note: Connection mode removed - click outlets/inlets directly
        // Click an outlet (bottom), then click an inlet (top) to connect
    }

    updateStatus(text, className) {
        const statusEl = document.getElementById('status');
        const statusText = statusEl.querySelector('.status-text');
        if (statusText) {
            statusText.textContent = text;
        }
        statusEl.className = className;
    }

    updateInfo(text) {
        const infoText = document.getElementById('info-text');
        if (infoText) {
            infoText.textContent = text;
        }
    }

    isHidden(box) {
        /** A box is hidden if it is a comment and comment-hiding is enabled. */
        return this.hideComments && !!box && box.maxclass === 'comment';
    }

    updateCommentsButton() {
        const btn = document.getElementById('toggle-comments-btn');
        if (btn) {
            btn.textContent = this.hideComments ? 'Show Comments' : 'Hide Comments';
            btn.classList.toggle('active', this.hideComments);
        }
    }

    updateLayoutModeButton() {
        const toggleBtn = document.getElementById('toggle-layout-mode-btn');
        if (toggleBtn) {
            const isSidebarMode = document.body.classList.contains('sidebar-mode');
            toggleBtn.textContent = isSidebarMode ? '📋 Panel Mode' : '⚙️ Sidebar Mode';
            toggleBtn.title = isSidebarMode
                ? 'Switch to horizontal panel layout'
                : 'Switch to right sidebar layout';
        }
    }

    handleUpdate(data) {
        if (data.type === 'update') {
            // Update title
            const patcherTitle = data.patcher_title || 'Untitled';
            document.getElementById('title').textContent =
                `py2max Interactive Editor - ${patcherTitle}`;

            // Re-engage auto-fit when the shown patcher changes (opened a file or
            // navigated in/out of a subpatcher) so new content is framed; but keep
            // the user's zoom/pan across edits to the *same* patcher.
            const patcherKey = `${(data.patcher_path || []).join('/')}::${patcherTitle}`;
            if (patcherKey !== this._viewPatcherKey) {
                this._viewPatcherKey = patcherKey;
                this.userAdjustedView = false;
            }

            // Update breadcrumb
            if (data.patcher_path && data.patcher_path.length > 0) {
                this.renderBreadcrumb(data.patcher_path);
            }

            // Update save button tooltip with filepath
            if (data.filepath) {
                this.currentFilepath = data.filepath;
                const saveBtn = document.getElementById('save-btn');
                if (saveBtn) {
                    saveBtn.title = `Save patch to ${data.filepath}`;
                }
            } else {
                this.currentFilepath = null;
            }

            // Clear current state
            this.boxes.clear();
            this.lines = [];

            // Update boxes
            data.boxes.forEach(box => {
                // Flatten patching_rect into box properties for easier access
                if (box.patching_rect) {
                    box.x = box.patching_rect.x;
                    box.y = box.patching_rect.y;
                    box.width = box.patching_rect.w;
                    box.height = box.patching_rect.h;
                }
                this.boxes.set(box.id, box);

                // Save original position if not already saved
                if (!this.originalPositions.has(box.id)) {
                    this.originalPositions.set(box.id, {
                        x: box.x,
                        y: box.y
                    });
                }
            });

            // Update lines
            this.lines = data.lines || [];

            // Re-render
            this.render();

            // Update info
            this.updateInfo(`${this.boxes.size} objects · ${this.lines.length} connections`);
        } else if (data.type === 'position_update') {
            // Delta update - only update the specific box position
            const box = this.boxes.get(data.box_id);
            if (box) {
                box.x = data.x;
                box.y = data.y;
                // Update the visual position without full re-render
                this.updateBoxPosition(data.box_id, data.x, data.y);
            }
        } else if (data.type === 'save_complete') {
            this.updateInfo(`Saved to ${data.filepath}`);
            console.log('Patch saved:', data.filepath);
            // Update the current filepath
            this.currentFilepath = data.filepath;
        } else if (data.type === 'save_as_required') {
            // No filepath set - show save dialog
            this.showSaveAsDialog();
        } else if (data.type === 'patch_content') {
            // Serialized patch returned for a client-side Save As.
            this.writePatchToFile(data.content);
        } else if (data.type === 'save_error') {
            this.updateInfo(`Save error: ${data.message}`);
            console.error('Save error:', data.message);
        } else if (data.type === 'open_error') {
            this.updateInfo(`Open error: ${data.message}`);
            console.error('Open error:', data.message);
        } else if (data.type === 'patch_list') {
            this.renderFileList(data);
        } else if (data.type === 'patch_list_error') {
            const status = document.getElementById('file-picker-status');
            if (status) status.textContent = data.message || 'Cannot read directory';
        }
    }

    render() {
        // Clear SVG using SVG.js
        this.linesGroupSVG.clear();
        this.boxesGroupSVG.clear();

        // Clear the <defs> too. createBox() adds a clipPath (id "clip-<boxId>")
        // per box on every render; without clearing defs, re-rendering leaks
        // duplicate ids and the browser resolves url(#clip-<id>) to the first,
        // stale clip at the box's old position -- clipping the label out of view
        // once a box moves. Clearing keeps clips fresh and correctly placed.
        const defs = this.draw.defs();
        if (defs) {
            defs.clear();
        }

        // Render patchlines first (behind boxes)
        this.lines.forEach(line => {
            const srcBox = this.boxes.get(line.src);
            const dstBox = this.boxes.get(line.dst);

            if (srcBox && dstBox && !this.isHidden(srcBox) && !this.isHidden(dstBox)) {
                const lineGroup = this.createLine(srcBox, dstBox, line);

                // Highlight if selected
                if (this.selectedLine &&
                    this.selectedLine.src === line.src &&
                    this.selectedLine.dst === line.dst &&
                    (this.selectedLine.src_outlet || 0) === (line.src_outlet || 0) &&
                    (this.selectedLine.dst_inlet || 0) === (line.dst_inlet || 0)) {
                    // Find the visible line element
                    const visibleLine = lineGroup.node.querySelector('.patchline');
                    if (visibleLine) {
                        SVG(visibleLine).stroke({ color: '#ff8040', width: 3 });
                    }
                }
            }
        });

        // Render boxes
        this.boxes.forEach(box => {
            if (this.isHidden(box)) return;

            const boxGroup = this.createBox(box);

            // Highlight if selected (single or part of a multi-selection)
            if (this.selectedBoxes.has(box.id)) {
                // Add selected class to disable hover styling
                boxGroup.node.classList.add('selected');
                const rect = boxGroup.node.querySelector('rect');
                if (rect) {
                    SVG(rect).stroke({ color: '#ff8040', width: 3 });
                }
            }
        });

        // Highlight selected port (inlet or outlet) if in connection mode
        if (this.connectionStart) {
            const portClass = this.connectionStart.isOutlet ? '.outlet' : '.inlet';
            const ports = this.boxesGroup.querySelectorAll(portClass);
            ports.forEach(port => {
                const parent = port.parentElement;
                const boxId = parent.getAttribute('data-id');
                const portIndex = parseInt(port.getAttribute('data-index'));

                if (boxId === this.connectionStart.box.id &&
                    portIndex === this.connectionStart.portIndex) {
                    port.classList.add('selected');
                }
            });
        }

        // Update viewBox to fit content
        this.updateViewBox();
    }

    createBox(box) {
        // Create group using SVG.js
        const g = this.boxesGroupSVG.group();
        g.addClass('box');
        g.attr('data-id', box.id);

        // Add special class for boxes with subpatchers
        if (box.has_subpatcher) {
            g.addClass('has-subpatcher');
        }

        const x = box.x || 0;
        const y = box.y || 0;
        const width = box.width || 60;
        const height = box.height || 22;

        // Create rectangle using SVG.js
        const rect = g.rect(width, height)
            .move(x, y)
            .fill(this.getBoxFill(box))
            .stroke({ color: '#333', width: 1 })
            .radius(3);

        // Create text using SVG.js
        const textContent = box.text || box.maxclass || '';
        const text = g.text(textContent)
            .attr({ x: x + 5, y: y + height / 2 + 4 })
            .fill('#000')
            .font({ family: 'Monaco, Courier, monospace', size: 11 })
            .attr('dominant-baseline', 'middle');

        // Add clipping using SVG.js
        const clipId = `clip-${box.id}`;
        const clip = this.draw.clip().id(clipId);
        clip.rect(width, height).move(x, y);
        text.clipWith(clip);

        // Add ports
        if (box.inlet_count > 0 || box.outlet_count > 0) {
            this.addPorts(g, box);
        }

        // Add interaction handlers to native DOM node
        g.node.addEventListener('mousedown', (e) => this.handleBoxMouseDown(e, box));
        // Note: dblclick is now handled via event delegation on boxesGroup

        return g;
    }

    addPorts(group, box) {
        const x = box.x || 0;
        const y = box.y || 0;
        const w = box.width || 60;
        const h = box.height || 22;

        // Draw inlets (top of box) using SVG.js
        if (box.inlet_count > 0) {
            const spacing = w / (box.inlet_count + 1);
            for (let i = 0; i < box.inlet_count; i++) {
                const circle = group.circle(8)  // diameter = 8, radius = 4
                    .center(x + spacing * (i + 1), y)
                    .fill('#4080ff')
                    .stroke({ color: '#333', width: 1 })
                    .addClass('inlet port')
                    .attr('data-index', i)
                    .css('cursor', 'pointer');

                // Native hover tooltip: maxref label if known, else generic.
                this.addPortTitle(circle.node, box.inlet_labels, i, 'Inlet');

                // Start a connection drag on press; a press-release without a
                // drag falls back to the two-click connect model (see mouseup).
                circle.node.addEventListener('mousedown', (e) => {
                    this.handlePortMouseDown(box, i, false, e);  // false = inlet
                });
            }
        }

        // Draw outlets (bottom of box) using SVG.js
        if (box.outlet_count > 0) {
            const spacing = w / (box.outlet_count + 1);
            for (let i = 0; i < box.outlet_count; i++) {
                const circle = group.circle(8)  // diameter = 8, radius = 4
                    .center(x + spacing * (i + 1), y + h)
                    .fill('#ff8040')
                    .stroke({ color: '#333', width: 1 })
                    .addClass('outlet port')
                    .attr('data-index', i)
                    .css('cursor', 'pointer');

                // Native hover tooltip: maxref label if known, else generic.
                this.addPortTitle(circle.node, box.outlet_labels, i, 'Outlet');

                // Start a connection drag on press; a press-release without a
                // drag falls back to the two-click connect model (see mouseup).
                circle.node.addEventListener('mousedown', (e) => {
                    this.handlePortMouseDown(box, i, true, e);  // true = outlet
                });
            }
        }
    }

    addPortTitle(portNode, labels, index, kind) {
        /**
         * Add a native SVG <title> (hover tooltip) to a port. Uses the maxref
         * label when present ("Inlet 0: Frequency"), else a generic "Inlet 0".
         */
        const label = (labels && labels[index]) ? String(labels[index]).trim() : '';
        const text = label ? `${kind} ${index}: ${label}` : `${kind} ${index}`;
        const titleEl = document.createElementNS(this.svgNS, 'title');
        titleEl.textContent = text;
        portNode.appendChild(titleEl);
    }

    createLine(srcBox, dstBox, line) {
        const srcPoint = this.getPortPosition(srcBox, line.src_outlet || 0, true);
        const dstPoint = this.getPortPosition(dstBox, line.dst_inlet || 0, false);

        // Create group for the line using SVG.js
        const g = this.linesGroupSVG.group();
        g.addClass('patchline-group');
        g.attr('data-src', line.src);
        g.attr('data-dst', line.dst);
        g.attr('data-src-outlet', line.src_outlet || 0);
        g.attr('data-dst-inlet', line.dst_inlet || 0);
        g.css('cursor', 'pointer');

        // Add invisible wider hitbox for easier clicking using SVG.js
        const hitbox = g.line(srcPoint.x, srcPoint.y, dstPoint.x, dstPoint.y)
            .stroke({ color: 'transparent', width: 10 })
            .addClass('patchline-hitbox');

        // Visible line using SVG.js
        const lineEl = g.line(srcPoint.x, srcPoint.y, dstPoint.x, dstPoint.y)
            .stroke({ color: '#666', width: 2, linecap: 'round' })
            .addClass('patchline');

        // Add click handler to the group (catches both hitbox and line clicks)
        g.node.addEventListener('click', (e) => {
            e.stopPropagation();
            this.handleLineClick(line);
        });

        return g;
    }

    getPortPosition(box, portIndex, isOutlet) {
        const x = box.x || 0;
        const y = box.y || 0;
        const w = box.width || 60;
        const h = box.height || 22;

        if (isOutlet) {
            const count = Math.max(1, box.outlet_count || 1);
            // Clamp port index to valid range
            const safeIndex = Math.max(0, Math.min(portIndex, count - 1));
            const spacing = w / (count + 1);
            return {
                x: x + spacing * (safeIndex + 1),
                y: y + h
            };
        } else {
            const count = Math.max(1, box.inlet_count || 1);
            // Clamp port index to valid range
            const safeIndex = Math.max(0, Math.min(portIndex, count - 1));
            const spacing = w / (count + 1);
            return {
                x: x + spacing * (safeIndex + 1),
                y: y
            };
        }
    }

    getBoxFill(box) {
        if (box.maxclass === 'comment') return '#ffffd0';
        if (box.maxclass === 'message') return '#e0e0e0';
        return '#f0f0f0';
    }

    updateBoxPosition(boxId, x, y) {
        /**
         * Efficiently update a single box position without full re-render.
         * Used for delta position updates from the server.
         */
        const boxElement = this.boxesGroup.querySelector(`[data-id="${boxId}"]`);
        if (!boxElement) return;

        const box = this.boxes.get(boxId);
        if (!box) return;

        const svgBox = SVG(boxElement);

        // Update rectangle position
        const rect = svgBox.findOne('rect');
        if (rect) {
            rect.move(x, y);
        }

        // Update text position
        const text = svgBox.findOne('text');
        if (text) {
            text.attr({ x: x + 5, y: y + (box.height || 22) / 2 + 4 });
        }

        // Update clip path. The clipPath lives in <defs> (not in the box group),
        // so locate it by id; otherwise the label's clip stays at the old
        // position and hides the text after a delta move.
        const clipEl = this.svg.querySelector(`#clip-${boxId} rect`);
        if (clipEl) {
            clipEl.setAttribute('x', x);
            clipEl.setAttribute('y', y);
        }

        // Update port positions
        const inlets = boxElement.querySelectorAll('.inlet');
        const outlets = boxElement.querySelectorAll('.outlet');
        const w = box.width || 60;
        const h = box.height || 22;

        if (inlets.length > 0) {
            const spacing = w / (inlets.length + 1);
            inlets.forEach((inlet, i) => {
                SVG(inlet).center(x + spacing * (i + 1), y);
            });
        }

        if (outlets.length > 0) {
            const spacing = w / (outlets.length + 1);
            outlets.forEach((outlet, i) => {
                SVG(outlet).center(x + spacing * (i + 1), y + h);
            });
        }

        // Update connected patchlines
        this.updateConnectedLines(boxId);
    }

    updateConnectedLines(boxId) {
        /**
         * Update all patchlines connected to a specific box.
         */
        this.lines.forEach(line => {
            if (line.src === boxId || line.dst === boxId) {
                const srcBox = this.boxes.get(line.src);
                const dstBox = this.boxes.get(line.dst);

                if (srcBox && dstBox) {
                    const srcPoint = this.getPortPosition(srcBox, line.src_outlet || 0, true);
                    const dstPoint = this.getPortPosition(dstBox, line.dst_inlet || 0, false);

                    // Find the line element
                    const lineGroup = this.linesGroup.querySelector(
                        `[data-src="${line.src}"][data-dst="${line.dst}"][data-src-outlet="${line.src_outlet || 0}"][data-dst-inlet="${line.dst_inlet || 0}"]`
                    );

                    if (lineGroup) {
                        const visibleLine = lineGroup.querySelector('.patchline');
                        const hitbox = lineGroup.querySelector('.patchline-hitbox');

                        if (visibleLine) {
                            visibleLine.setAttribute('x1', srcPoint.x);
                            visibleLine.setAttribute('y1', srcPoint.y);
                            visibleLine.setAttribute('x2', dstPoint.x);
                            visibleLine.setAttribute('y2', dstPoint.y);
                        }
                        if (hitbox) {
                            hitbox.setAttribute('x1', srcPoint.x);
                            hitbox.setAttribute('y1', srcPoint.y);
                            hitbox.setAttribute('x2', dstPoint.x);
                            hitbox.setAttribute('y2', dstPoint.y);
                        }
                    }
                }
            }
        });
    }

    updateViewBox() {
        // If the user has panned/zoomed, leave the view alone so an edit
        // (create/connect/delete) or a live server update doesn't yank it back
        // to auto-fit. Press "f" (fitView) to re-engage auto-fit.
        if (this.userAdjustedView) {
            return;
        }

        if (this.boxes.size === 0) {
            this.svg.setAttribute('viewBox', '0 0 1200 800');
            return;
        }

        let minX = Infinity, minY = Infinity;
        let maxX = -Infinity, maxY = -Infinity;

        this.boxes.forEach(box => {
            if (this.isHidden(box)) return;  // don't let hidden comments drive the fit

            const x = box.x || 0;
            const y = box.y || 0;
            const w = box.width || 60;
            const h = box.height || 22;

            minX = Math.min(minX, x);
            minY = Math.min(minY, y);
            maxX = Math.max(maxX, x + w);
            maxY = Math.max(maxY, y + h);
        });

        // Nothing visible (e.g. a patch of only comments, all hidden).
        if (maxX === -Infinity) {
            this.svg.setAttribute('viewBox', '0 0 1200 800');
            return;
        }

        // Calculate content dimensions
        const contentWidth = maxX - minX;
        const contentHeight = maxY - minY;

        // Dynamic padding: 10% of content size, minimum 30px, maximum 100px
        const padding = Math.max(30, Math.min(100, Math.min(contentWidth, contentHeight) * 0.1));

        minX -= padding;
        minY -= padding;
        maxX += padding;
        maxY += padding;

        // Ensure viewBox doesn't shift below/left of origin
        // Keep at least some visible area starting from (0, 0)
        minX = Math.min(minX, 0);
        minY = Math.min(minY, 0);

        // Ensure minimum viewBox size to prevent cramped layouts
        // Read from sliders if available, otherwise use defaults
        const minViewWidth = parseInt(document.getElementById('min-viewbox-width-slider')?.value || 400);
        const minViewHeight = parseInt(document.getElementById('min-viewbox-height-slider')?.value || 300);

        let width = Math.max(maxX - minX, minViewWidth);
        let height = Math.max(maxY - minY, minViewHeight);

        // Preserve aspect ratio based on canvas dimensions
        const canvas = document.getElementById('canvas');
        if (canvas && canvas.clientWidth > 0 && canvas.clientHeight > 0) {
            const canvasAspect = canvas.clientWidth / canvas.clientHeight;
            const viewAspect = width / height;

            if (viewAspect > canvasAspect) {
                // ViewBox is wider than canvas - increase height to match
                const newHeight = width / canvasAspect;
                const heightDiff = newHeight - height;
                minY -= heightDiff / 2;
                height = newHeight;
            } else if (viewAspect < canvasAspect) {
                // ViewBox is taller than canvas - increase width to match
                const newWidth = height * canvasAspect;
                const widthDiff = newWidth - width;
                minX -= widthDiff / 2;
                width = newWidth;
            }
        }

        this.svg.setAttribute('viewBox', `${minX} ${minY} ${width} ${height}`);
    }

    // Event handlers for drag-and-drop

    handleBoxMouseDown(event, box) {
        // While panning (Space-drag or middle-mouse), let the event bubble to the
        // canvas handler to pan instead of selecting/dragging this box. Returning
        // without stopPropagation lets it reach handleCanvasMouseDown.
        if (this.spaceDown || event.button === 1) {
            return;
        }

        // Don't handle if clicking on a port - let port handler deal with it
        if (event.target.classList.contains('port') ||
            event.target.classList.contains('inlet') ||
            event.target.classList.contains('outlet')) {
            return;
        }

        // Stop propagation for ALL boxes to prevent canvas handler from running
        event.stopPropagation();

        // Manual double-click detection. handleBoxMouseDown re-renders (rebuilding
        // the box DOM) on every press, which makes the native dblclick event
        // unreliable, so detect it here instead: two presses on the same box in
        // quick succession -> open subpatcher, or edit the object's text.
        const now = performance.now();
        if (this._lastBoxClick &&
            this._lastBoxClick.id === box.id &&
            (now - this._lastBoxClick.time) < 350) {
            this._lastBoxClick = null;
            this.dragging = false;
            this.dragStarted = false;
            event.preventDefault();
            if (box.has_subpatcher) {
                this.navigateToSubpatcher(box.id);
            } else {
                this.startTextEdit(box, event.currentTarget);
            }
            return;
        }
        this._lastBoxClick = { id: box.id, time: now };

        const svgPoint = this.getSVGPoint(event);
        this.selectedLine = null;

        // Shift-click toggles this box in the selection and does NOT start a drag.
        if (event.shiftKey) {
            if (this.selectedBoxes.has(box.id)) {
                this.selectedBoxes.delete(box.id);
                if (this.selectedBox && this.selectedBox.id === box.id) this.selectedBox = null;
            } else {
                this.selectedBoxes.add(box.id);
                this.selectedBox = box;
            }
            this.dragging = false;
            this.updateInfo(`${this.selectedBoxes.size} object(s) selected`);
            this.render();
            return;
        }

        // Non-shift press: if the box isn't part of the current multi-selection,
        // select it alone. If it already is, keep the whole group so it can be
        // dragged together (a click without drag collapses to it on mouseup).
        if (!this.selectedBoxes.has(box.id)) {
            this.selectedBoxes.clear();
            this.selectedBoxes.add(box.id);
        }
        this.selectedBox = box;
        this.dragAnchorId = box.id;

        // Prepare a (possibly group) drag: snapshot start positions of all
        // selected boxes so the move applies the same delta to each.
        this.dragging = true;
        this.dragStarted = false;  // Not started until movement
        this.mouseDownPos = { x: svgPoint.x, y: svgPoint.y };
        this.dragBoxes = [];
        this.selectedBoxes.forEach(id => {
            const b = this.boxes.get(id);
            if (b) this.dragBoxes.push({ id, startX: b.x || 0, startY: b.y || 0 });
        });

        // Show selection immediately (before drag starts)
        this.render();
    }

    handleCanvasMouseMove(event) {
        if (this.panning && this.panStart) {
            // Convert the pixel delta since pan-start into SVG units (CTM.a/.d is
            // screen-per-SVG scale, constant while panning since w/h don't change)
            // and shift the viewBox opposite to the drag so content tracks the
            // cursor. Anchored to the start viewBox to avoid drift.
            const CTM = this.svg.getScreenCTM();
            const dxSvg = (event.clientX - this.panStart.clientX) / CTM.a;
            const dySvg = (event.clientY - this.panStart.clientY) / CTM.d;
            const vb = this.panStart.vb;
            this.setViewBox(vb.x - dxSvg, vb.y - dySvg, vb.w, vb.h);
            this.userAdjustedView = true;
            return;
        }

        if (this.connectionDrag) {
            this.updateConnectionPreview(event);
            return;
        }

        if (this.marquee) {
            this.updateMarquee(event);
            return;
        }

        if (this.dragging && this.dragBoxes && this.dragBoxes.length) {
            const svgPoint = this.getSVGPoint(event);

            // Check if we've moved enough to start dragging (5px threshold)
            if (!this.dragStarted && this.mouseDownPos) {
                const dx = Math.abs(svgPoint.x - this.mouseDownPos.x);
                const dy = Math.abs(svgPoint.y - this.mouseDownPos.y);
                if (dx > 5 || dy > 5) {
                    this.dragStarted = true;
                }
            }

            // Apply the same delta to every selected box via the incremental
            // path (moves existing elements, so selection strokes travel along),
            // instead of rebuilding the whole SVG each frame.
            if (this.dragStarted) {
                const dx = svgPoint.x - this.mouseDownPos.x;
                const dy = svgPoint.y - this.mouseDownPos.y;
                this.dragBoxes.forEach(d => {
                    const b = this.boxes.get(d.id);
                    if (!b) return;
                    b.x = d.startX + dx;
                    b.y = d.startY + dy;
                    this.updateBoxPosition(d.id, b.x, b.y);
                });
            }
        }
    }

    handleCanvasMouseUp(event) {
        if (this.panning) {
            this.endPan();
            return;
        }

        if (this.connectionDrag) {
            this.finishConnectionDrag(event);
            return;
        }

        if (this.marquee) {
            this.finishMarquee(event);
            return;
        }

        if (this.dragging && this.dragBoxes && this.dragBoxes.length) {
            if (this.dragStarted) {
                // Send one batch for a group move, or a single update for one box
                // (so single-box moves keep their light-weight delta path).
                const positions = this.dragBoxes
                    .map(d => {
                        const b = this.boxes.get(d.id);
                        return b ? { box_id: d.id, x: b.x, y: b.y } : null;
                    })
                    .filter(Boolean);

                if (positions.length === 1) {
                    this.sendMessage({ type: 'update_position', ...positions[0] });
                } else if (positions.length > 1) {
                    this.sendMessage({ type: 'update_positions', positions });
                }
                // Keep the selection so "move then delete" works without re-selecting.
                this.updateInfo(`Moved ${positions.length} object(s) (Press Delete to remove)`);
            } else {
                // Click without drag: collapse a multi-selection to just this box.
                this.selectedBoxes.clear();
                if (this.dragAnchorId) this.selectedBoxes.add(this.dragAnchorId);
                this.selectedBox = this.boxes.get(this.dragAnchorId) || null;
                this.updateInfo('Selected (Press Delete to remove)');
                this.render();
            }

            this.dragging = false;
            this.dragStarted = false;
            this.mouseDownPos = null;
            this.dragBoxes = null;
        }
    }

    handlePortMouseDown(box, portIndex, isOutlet, event) {
        // Let Space-drag / middle-mouse pan even when starting over a port.
        if (this.spaceDown || event.button === 1) {
            return;
        }
        event.stopPropagation();
        event.preventDefault();

        const start = this.getPortPosition(box, portIndex, isOutlet);
        this.connectionDrag = { box, portIndex, isOutlet, start, moved: false };

        // Rubber-band cord from the armed port to the cursor.
        this.previewLine = this.draw
            .line(start.x, start.y, start.x, start.y)
            .stroke({ color: '#ff8040', width: 2, dasharray: '4,3' })
            .addClass('connection-preview');
        this.previewLine.attr('pointer-events', 'none');

        // Highlight all compatible (opposite-type) targets for the drag.
        const targetClass = isOutlet ? 'inlet' : 'outlet';
        this.boxesGroup.querySelectorAll('.' + targetClass).forEach(el => {
            el.classList.add('connect-compatible');
        });

        const portType = isOutlet ? 'outlet' : 'inlet';
        const targetType = isOutlet ? 'inlet' : 'outlet';
        this.updateInfo(`Drag from ${box.text || box.id} ${portType} ${portIndex} to an ${targetType} (or click to arm)`);
    }

    updateConnectionPreview(event) {
        const pt = this.getSVGPoint(event);
        const start = this.connectionDrag.start;

        // Past a small threshold, treat it as a real drag (not a click).
        if (!this.connectionDrag.moved) {
            const dx = Math.abs(pt.x - start.x);
            const dy = Math.abs(pt.y - start.y);
            if (dx > 5 || dy > 5) this.connectionDrag.moved = true;
        }

        if (this.previewLine) {
            this.previewLine.plot(start.x, start.y, pt.x, pt.y);
        }

        // Strong-highlight the compatible port directly under the cursor.
        if (this._hoverPort) {
            this._hoverPort.classList.remove('connect-hover');
            this._hoverPort = null;
        }
        const info = this.portInfoFromPoint(event.clientX, event.clientY);
        if (info && info.isOutlet !== this.connectionDrag.isOutlet) {
            info.el.classList.add('connect-hover');
            this._hoverPort = info.el;
        }
    }

    finishConnectionDrag(event) {
        const drag = this.connectionDrag;
        const target = this.portInfoFromPoint(event.clientX, event.clientY);

        // Clear preview + highlights first so any subsequent render is clean.
        this.clearConnectionDrag();

        if (drag.moved) {
            // Drag-to-connect: require a compatible (opposite-type) target port.
            if (target && target.isOutlet !== drag.isOutlet) {
                this.makeConnection(
                    drag.isOutlet
                        ? { box: drag.box, portIndex: drag.portIndex }
                        : { boxId: target.boxId, portIndex: target.index },
                    drag.isOutlet
                        ? { boxId: target.boxId, portIndex: target.index }
                        : { box: drag.box, portIndex: drag.portIndex }
                );
            } else {
                this.updateInfo('Connection cancelled');
            }
        } else {
            // No movement: fall back to the two-click arm/complete model.
            this.handlePortClick(drag.box, drag.portIndex, drag.isOutlet);
        }
    }

    clearConnectionDrag() {
        if (this.previewLine) {
            this.previewLine.remove();
            this.previewLine = null;
        }
        this.boxesGroup.querySelectorAll('.connect-compatible').forEach(el => {
            el.classList.remove('connect-compatible');
        });
        if (this._hoverPort) {
            this._hoverPort.classList.remove('connect-hover');
            this._hoverPort = null;
        }
        this.connectionDrag = null;
    }

    portInfoFromPoint(clientX, clientY) {
        /** Resolve the port element at a screen point to {el, boxId, index, isOutlet}. */
        const el = document.elementFromPoint(clientX, clientY);
        if (!el || !el.classList) return null;
        const isInlet = el.classList.contains('inlet');
        const isOutlet = el.classList.contains('outlet');
        if (!isInlet && !isOutlet) return null;
        const group = el.closest('[data-id]');
        if (!group) return null;
        return {
            el,
            boxId: group.getAttribute('data-id'),
            index: parseInt(el.getAttribute('data-index') || '0', 10),
            isOutlet
        };
    }

    makeConnection(src, dst) {
        /**
         * Send a create_connection. src/dst are {box|boxId, portIndex}; src is the
         * outlet side, dst the inlet side.
         */
        const srcId = src.box ? src.box.id : src.boxId;
        const dstId = dst.box ? dst.box.id : dst.boxId;
        const srcBox = this.boxes.get(srcId);
        const dstBox = this.boxes.get(dstId);
        this.sendMessage({
            type: 'create_connection',
            src_id: srcId,
            dst_id: dstId,
            src_outlet: src.portIndex,
            dst_inlet: dst.portIndex
        });
        this.updateInfo(`Connected: ${srcBox?.text || srcId}[${src.portIndex}] -> ${dstBox?.text || dstId}[${dst.portIndex}]`);
    }

    handlePortClick(box, portIndex, isOutlet) {
        if (!this.connectionStart) {
            // First click - can be either inlet or outlet
            this.connectionStart = {
                box: box,
                portIndex: portIndex,
                isOutlet: isOutlet
            };

            const portType = isOutlet ? 'outlet' : 'inlet';
            const nextType = isOutlet ? 'inlet' : 'outlet';
            this.updateInfo(`Connecting from ${box.text || box.id} ${portType} ${portIndex}... Click ${nextType}`);

            // Visual feedback - highlight the selected port
            this.render();
        } else {
            // Second click - must be opposite type from first click
            if (isOutlet === this.connectionStart.isOutlet) {
                const portType = isOutlet ? 'outlet' : 'inlet';
                const oppositeType = isOutlet ? 'inlet' : 'outlet';
                this.updateInfo(`Click an ${oppositeType}, not another ${portType}`);
                return;
            }

            // Determine source (outlet) and destination (inlet)
            let srcBox, dstBox, srcOutlet, dstInlet;

            if (this.connectionStart.isOutlet) {
                // Started from outlet, ending at inlet
                srcBox = this.connectionStart.box;
                srcOutlet = this.connectionStart.portIndex;
                dstBox = box;
                dstInlet = portIndex;
            } else {
                // Started from inlet, ending at outlet
                srcBox = box;
                srcOutlet = portIndex;
                dstBox = this.connectionStart.box;
                dstInlet = this.connectionStart.portIndex;
            }

            // Create connection
            this.sendMessage({
                type: 'create_connection',
                src_id: srcBox.id,
                dst_id: dstBox.id,
                src_outlet: srcOutlet,
                dst_inlet: dstInlet
            });

            this.updateInfo(`Connected: ${srcBox.text}[${srcOutlet}] → ${dstBox.text}[${dstInlet}]`);

            // Clear connection state
            this.connectionStart = null;
            this.render();
        }
    }

    handleCanvasDoubleClick(event) {
        // Only create an object when double-clicking empty canvas; box
        // double-clicks are handled in handleBoxMouseDown.
        if (event.target.closest && event.target.closest('.box')) {
            return;
        }
        const svgPoint = this.getSVGPoint(event);
        this.createObjectDialog(svgPoint.x, svgPoint.y);
    }

    handleLineClick(line) {
        // Select the line for deletion
        this.selectedLine = line;
        this.selectedBox = null;  // Deselect any box(es)
        this.selectedBoxes.clear();

        const srcBox = this.boxes.get(line.src);
        const dstBox = this.boxes.get(line.dst);
        this.updateInfo(`Selected connection: ${srcBox.text}[${line.src_outlet}] → ${dstBox.text}[${line.dst_inlet}] (Press Delete/Backspace to remove)`);

        // Re-render to show selection
        this.render();
    }

    handleCanvasMouseDown(event) {
        const target = event.target;

        // Pan with Space-drag or middle-mouse, anywhere on the canvas (even over
        // boxes/ports/lines), before any selection logic runs.
        if (this.spaceDown || event.button === 1) {
            event.preventDefault();
            this.startPan(event);
            return;
        }

        // Check if clicking on a port - if so, don't deselect or cancel connection
        if (target.classList.contains('port') ||
            target.classList.contains('inlet') ||
            target.classList.contains('outlet')) {
            // Clicking on a port - let the port's click handler handle it
            return;
        }

        // Check if clicking on a line - if so, don't deselect
        if (target.classList.contains('patchline') ||
            target.classList.contains('patchline-hitbox') ||
            target.closest('.patchline-group')) {
            // Clicking on a line - don't deselect, let the line's click handler handle it
            return;
        }

        // Empty-canvas press: start a marquee (rubber-band) selection. Shift
        // keeps the current selection (additive); otherwise clear it.
        if (!event.shiftKey) {
            this.selectedBoxes.clear();
            this.selectedBox = null;
        }
        this.selectedLine = null;

        // Cancel any pending two-click connection
        if (this.connectionStart) {
            this.connectionStart = null;
            this.updateInfo('Connection cancelled');
        }

        const svgPoint = this.getSVGPoint(event);
        this.marquee = {
            startX: svgPoint.x,
            startY: svgPoint.y,
            shift: event.shiftKey,
            moved: false,
            rectEl: null
        };

        this.render();
    }

    updateMarquee(event) {
        const pt = this.getSVGPoint(event);
        const m = this.marquee;
        const x = Math.min(m.startX, pt.x);
        const y = Math.min(m.startY, pt.y);
        const w = Math.abs(pt.x - m.startX);
        const h = Math.abs(pt.y - m.startY);
        if (w > 3 || h > 3) m.moved = true;

        if (!m.rectEl) {
            m.rectEl = this.draw.rect(w, h)
                .addClass('marquee')
                .fill({ color: '#4080ff', opacity: 0.15 })
                .stroke({ color: '#4080ff', width: 1, dasharray: '4,3' });
            m.rectEl.attr('pointer-events', 'none');
        }
        m.rectEl.move(x, y).size(w, h);
    }

    finishMarquee(event) {
        const m = this.marquee;
        if (m.rectEl) m.rectEl.remove();
        this.marquee = null;

        if (m.moved) {
            const pt = this.getSVGPoint(event);
            const rx = Math.min(m.startX, pt.x);
            const ry = Math.min(m.startY, pt.y);
            const rw = Math.abs(pt.x - m.startX);
            const rh = Math.abs(pt.y - m.startY);

            // Select every visible box whose rectangle intersects the marquee.
            this.boxes.forEach((b, id) => {
                if (this.isHidden(b)) return;
                const bx = b.x || 0, by = b.y || 0;
                const bw = b.width || 60, bh = b.height || 22;
                const intersects = !(bx + bw < rx || bx > rx + rw || by + bh < ry || by > ry + rh);
                if (intersects) {
                    this.selectedBoxes.add(id);
                    this.selectedBox = b;
                }
            });
            this.updateInfo(`${this.selectedBoxes.size} object(s) selected`);
        }

        this.render();
    }

    handleDelete() {
        if (this.selectedLine) {
            // Delete selected patchline
            this.sendMessage({
                type: 'delete_connection',
                src_id: this.selectedLine.src,
                dst_id: this.selectedLine.dst,
                src_outlet: this.selectedLine.src_outlet,
                dst_inlet: this.selectedLine.dst_inlet
            });

            this.updateInfo('Connection deleted');
            this.selectedLine = null;
        } else if (this.selectedBoxes.size > 0) {
            // Delete all selected boxes: one batch (one undo step) for a group,
            // or a single delete for one box.
            const ids = Array.from(this.selectedBoxes);
            if (ids.length === 1) {
                this.sendMessage({ type: 'delete_object', box_id: ids[0] });
            } else {
                this.sendMessage({ type: 'delete_objects', box_ids: ids });
            }
            this.updateInfo(`Deleted ${ids.length} object(s)`);
            this.selectedBoxes.clear();
            this.selectedBox = null;
        } else {
            this.updateInfo('Nothing selected to delete');
        }
    }

    // Navigation methods

    handleBoxDoubleClick(event, box) {
        event.stopPropagation();
        event.preventDefault();

        // If box has subpatcher, navigate to it
        if (box.has_subpatcher) {
            this.navigateToSubpatcher(box.id);
        }
    }

    handleBoxesGroupDoubleClick(event) {
        // Event delegation: find which box was double-clicked
        let target = event.target;
        let boxElement = null;

        // Walk up the DOM tree to find the box group element
        while (target && target !== this.boxesGroup) {
            if (target.classList && target.classList.contains('box')) {
                boxElement = target;
                break;
            }
            target = target.parentElement;
        }

        if (boxElement) {
            const boxId = boxElement.getAttribute('data-id');
            const box = this.boxes.get(boxId);

            if (box) {
                event.stopPropagation();
                event.preventDefault();
                if (box.has_subpatcher) {
                    this.navigateToSubpatcher(box.id);
                } else {
                    // Double-click a plain object to edit its text in place.
                    this.startTextEdit(box, boxElement);
                }
            }
        }
    }

    startTextEdit(box, boxElement) {
        /**
         * Overlay an <input> on the box to edit its text. Commits on Enter/blur,
         * cancels on Escape. Sends edit_object_text to the server on commit.
         */
        if (!boxElement) {
            boxElement = this.boxesGroup.querySelector(`[data-id="${box.id}"]`);
        }
        if (!boxElement) return;

        const rect = boxElement.getBoundingClientRect();

        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'inline-edit';
        input.value = box.text || '';
        input.style.left = `${rect.left}px`;
        input.style.top = `${rect.top}px`;
        input.style.width = `${Math.max(rect.width, 80)}px`;
        input.style.height = `${rect.height}px`;
        document.body.appendChild(input);
        input.focus();
        input.select();

        let done = false;
        const finish = (save) => {
            if (done) return;
            done = true;
            const newText = input.value;
            input.remove();
            if (save && newText !== (box.text || '')) {
                this.sendMessage({
                    type: 'edit_object_text',
                    box_id: box.id,
                    text: newText
                });
                this.updateInfo(`Edited "${box.text || box.id}" -> "${newText}"`);
            } else {
                this.updateInfo('Edit cancelled');
            }
        };

        input.addEventListener('keydown', (e) => {
            // Keep global handlers (Delete/Backspace/Escape) from firing while typing.
            e.stopPropagation();
            if (e.key === 'Enter') {
                e.preventDefault();
                finish(true);
            } else if (e.key === 'Escape') {
                e.preventDefault();
                finish(false);
            }
        });
        input.addEventListener('blur', () => finish(true));
    }

    navigateToSubpatcher(boxId) {
        this.sendMessage({
            type: 'navigate_to_subpatcher',
            box_id: boxId
        });
        this.updateInfo('Navigating to subpatcher...');
    }

    navigateToParent() {
        this.sendMessage({
            type: 'navigate_to_parent'
        });
        this.updateInfo('Navigating to parent...');
    }

    navigateToRoot() {
        this.sendMessage({
            type: 'navigate_to_root'
        });
        this.updateInfo('Navigating to root...');
    }

    navigateUp(levels) {
        this.sendMessage({
            type: 'navigate_up',
            levels: levels
        });
        this.updateInfo(`Navigating up ${levels} level(s)...`);
    }

    renderBreadcrumb(path) {
        /**
         * Render the patcher path as clickable crumbs. Each ancestor jumps to
         * that patcher: the root crumb via navigate_to_root, intermediate crumbs
         * via navigate_up (levels from the current depth). The last crumb is the
         * current patcher and is not clickable. Text is set via textContent so a
         * patcher title can never inject markup.
         */
        const container = document.getElementById('breadcrumb-path');
        if (!container) return;

        container.textContent = '';
        const last = path.length - 1;

        path.forEach((name, i) => {
            if (i > 0) {
                const sep = document.createElement('span');
                sep.className = 'breadcrumb-sep';
                sep.textContent = ' / ';
                container.appendChild(sep);
            }

            const crumb = document.createElement('span');
            crumb.textContent = name || 'Untitled';

            if (i === last) {
                crumb.className = 'breadcrumb-current';
            } else {
                crumb.className = 'breadcrumb-link';
                crumb.setAttribute('role', 'button');
                crumb.title = `Go to ${name}`;
                const levelsUp = last - i;
                crumb.addEventListener('click', () => {
                    if (i === 0) {
                        this.navigateToRoot();
                    } else {
                        this.navigateUp(levelsUp);
                    }
                });
            }

            container.appendChild(crumb);
        });
    }

    initializeLayoutControls() {
        // Link Distance slider
        const linkDistanceSlider = document.getElementById('link-distance-slider');
        const linkDistanceValue = document.getElementById('link-distance-value');
        if (linkDistanceSlider && linkDistanceValue) {
            linkDistanceSlider.addEventListener('input', (e) => {
                linkDistanceValue.textContent = e.target.value;
            });
        }

        // Iterations slider
        const iterationsSlider = document.getElementById('iterations-slider');
        const iterationsValue = document.getElementById('iterations-value');
        if (iterationsSlider && iterationsValue) {
            iterationsSlider.addEventListener('input', (e) => {
                iterationsValue.textContent = e.target.value;
            });
        }

        // Canvas Width slider
        const canvasWidthSlider = document.getElementById('canvas-width-slider');
        const canvasWidthValue = document.getElementById('canvas-width-value');
        if (canvasWidthSlider && canvasWidthValue) {
            canvasWidthSlider.addEventListener('input', (e) => {
                canvasWidthValue.textContent = e.target.value;
            });
        }

        // Canvas Height slider
        const canvasHeightSlider = document.getElementById('canvas-height-slider');
        const canvasHeightValue = document.getElementById('canvas-height-value');
        if (canvasHeightSlider && canvasHeightValue) {
            canvasHeightSlider.addEventListener('input', (e) => {
                canvasHeightValue.textContent = e.target.value;
            });
        }

        // Min ViewBox Width slider
        const minViewboxWidthSlider = document.getElementById('min-viewbox-width-slider');
        const minViewboxWidthValue = document.getElementById('min-viewbox-width-value');
        if (minViewboxWidthSlider && minViewboxWidthValue) {
            minViewboxWidthSlider.addEventListener('input', (e) => {
                minViewboxWidthValue.textContent = e.target.value;
                this.render();  // Re-render to update viewBox
            });
        }

        // Min ViewBox Height slider
        const minViewboxHeightSlider = document.getElementById('min-viewbox-height-slider');
        const minViewboxHeightValue = document.getElementById('min-viewbox-height-value');
        if (minViewboxHeightSlider && minViewboxHeightValue) {
            minViewboxHeightSlider.addEventListener('input', (e) => {
                minViewboxHeightValue.textContent = e.target.value;
                this.render();  // Re-render to update viewBox
            });
        }

        // Flow Spacing slider
        const flowSpacingSlider = document.getElementById('flow-spacing-slider');
        const flowSpacingValue = document.getElementById('flow-spacing-value');
        if (flowSpacingSlider && flowSpacingValue) {
            flowSpacingSlider.addEventListener('input', (e) => {
                flowSpacingValue.textContent = e.target.value;
            });
        }

        // Layout Engine selector - toggle parameter visibility
        const layoutEngineSelector = document.getElementById('layout-engine');
        if (layoutEngineSelector) {
            layoutEngineSelector.addEventListener('change', (e) => {
                this.toggleLayoutParameters(e.target.value);
            });
            // Set initial state
            this.toggleLayoutParameters(layoutEngineSelector.value);
        }

        // Apply Layout button
        const applyLayoutBtn = document.getElementById('apply-layout-btn');
        if (applyLayoutBtn) {
            applyLayoutBtn.addEventListener('click', () => {
                this.autoLayout();
            });
        }

        // Reset Layout button
        const resetLayoutBtn = document.getElementById('reset-layout-btn');
        if (resetLayoutBtn) {
            resetLayoutBtn.addEventListener('click', () => {
                this.resetLayout();
            });
        }

        // Advanced disclosure - collapse the engine-internal parameters.
        const advancedToggle = document.getElementById('advanced-toggle');
        const advancedBody = document.getElementById('advanced-body');
        if (advancedToggle && advancedBody) {
            advancedToggle.addEventListener('click', () => {
                const collapsed = advancedBody.classList.toggle('collapsed');
                advancedToggle.textContent = collapsed ? '+ Advanced' : '- Advanced';
                advancedToggle.setAttribute('aria-expanded', String(!collapsed));
            });
        }
    }

    reverseFlowLayout(nodes, axis, canvasWidth, canvasHeight) {
        /**
         * Reverse the flow layout by flipping coordinates along the specified axis.
         * For 'x' axis: flip horizontally (right-to-left)
         * For 'y' axis: flip vertically (bottom-to-top)
         */
        if (axis === 'x') {
            // Flip horizontally: mirror around vertical center
            const centerX = canvasWidth / 2;
            nodes.forEach(node => {
                const distanceFromCenter = node.x - centerX;
                node.x = centerX - distanceFromCenter;
            });
        } else if (axis === 'y') {
            // Flip vertically: mirror around horizontal center
            const centerY = canvasHeight / 2;
            nodes.forEach(node => {
                const distanceFromCenter = node.y - centerY;
                node.y = centerY - distanceFromCenter;
            });
        }
    }

    generateConstraints(nodes, preset) {
        const constraints = [];

        if (preset === 'none' || nodes.length === 0) {
            return constraints;
        }

        // Sort nodes by their current position for alignment
        const sortedByY = [...nodes].sort((a, b) => a.y - b.y);
        const sortedByX = [...nodes].sort((a, b) => a.x - b.x);

        if (preset === 'horizontal-flow') {
            // Align nodes in horizontal rows (same y coordinate for groups)
            // Group nodes into rows based on Y proximity
            const rows = [];
            const threshold = 50; // Y-distance threshold for same row

            sortedByY.forEach(node => {
                let addedToRow = false;
                for (let row of rows) {
                    const avgY = row.reduce((sum, n) => sum + n.y, 0) / row.length;
                    if (Math.abs(node.y - avgY) < threshold) {
                        row.push(node);
                        addedToRow = true;
                        break;
                    }
                }
                if (!addedToRow) {
                    rows.push([node]);
                }
            });

            // Create alignment constraints for each row
            rows.forEach(row => {
                if (row.length > 1) {
                    constraints.push({
                        type: 'alignment',
                        axis: 'y',
                        offsets: row.map(n => ({ node: nodes.indexOf(n), offset: 0 }))
                    });
                }
            });

        } else if (preset === 'vertical-flow') {
            // Align nodes in vertical columns (same x coordinate for groups)
            const columns = [];
            const threshold = 50; // X-distance threshold for same column

            sortedByX.forEach(node => {
                let addedToColumn = false;
                for (let column of columns) {
                    const avgX = column.reduce((sum, n) => sum + n.x, 0) / column.length;
                    if (Math.abs(node.x - avgX) < threshold) {
                        column.push(node);
                        addedToColumn = true;
                        break;
                    }
                }
                if (!addedToColumn) {
                    columns.push([node]);
                }
            });

            // Create alignment constraints for each column
            columns.forEach(column => {
                if (column.length > 1) {
                    constraints.push({
                        type: 'alignment',
                        axis: 'x',
                        offsets: column.map(n => ({ node: nodes.indexOf(n), offset: 0 }))
                    });
                }
            });

        } else if (preset === 'grid') {
            // Create both horizontal and vertical alignment constraints
            // This creates a grid-like structure

            // Horizontal alignment (rows)
            const rows = [];
            const yThreshold = 50;

            sortedByY.forEach(node => {
                let addedToRow = false;
                for (let row of rows) {
                    const avgY = row.reduce((sum, n) => sum + n.y, 0) / row.length;
                    if (Math.abs(node.y - avgY) < yThreshold) {
                        row.push(node);
                        addedToRow = true;
                        break;
                    }
                }
                if (!addedToRow) {
                    rows.push([node]);
                }
            });

            rows.forEach(row => {
                if (row.length > 1) {
                    constraints.push({
                        type: 'alignment',
                        axis: 'y',
                        offsets: row.map(n => ({ node: nodes.indexOf(n), offset: 0 }))
                    });
                }
            });

            // Vertical alignment (columns)
            const columns = [];
            const xThreshold = 50;

            sortedByX.forEach(node => {
                let addedToColumn = false;
                for (let column of columns) {
                    const avgX = column.reduce((sum, n) => sum + n.x, 0) / column.length;
                    if (Math.abs(node.x - avgX) < xThreshold) {
                        column.push(node);
                        addedToColumn = true;
                        break;
                    }
                }
                if (!addedToColumn) {
                    columns.push([node]);
                }
            });

            columns.forEach(column => {
                if (column.length > 1) {
                    constraints.push({
                        type: 'alignment',
                        axis: 'x',
                        offsets: column.map(n => ({ node: nodes.indexOf(n), offset: 0 }))
                    });
                }
            });
        }

        console.log(`Generated ${constraints.length} constraints for preset: ${preset}`);
        return constraints;
    }

    resetLayout() {
        /**
         * Reset all boxes to their original positions from when the patch was loaded.
         */
        if (this.originalPositions.size === 0) {
            this.updateInfo('No original positions to reset to');
            return;
        }

        this.updateInfo('Resetting to original layout...');

        // Restore original positions for all boxes
        this.boxes.forEach((box, boxId) => {
            const original = this.originalPositions.get(boxId);
            if (original) {
                box.x = original.x;
                box.y = original.y;

                // Send position update to server
                this.sendMessage({
                    type: 'update_position',
                    box_id: boxId,
                    x: original.x,
                    y: original.y
                });
            }
        });

        // Re-frame the restored layout (drop manual zoom/pan) and re-render.
        this.userAdjustedView = false;
        this.render();
        this.updateInfo(`Reset ${this.originalPositions.size} objects to original positions`);
    }

    autoLayout() {
        if (this.boxes.size === 0) {
            this.updateInfo('No objects to layout');
            return;
        }

        // Applying a layout rearranges everything, so re-frame the result: drop
        // any manual zoom/pan so the post-layout render auto-fits.
        this.userAdjustedView = false;

        // Check which layout engine is selected
        const layoutEngine = document.getElementById('layout-engine')?.value || 'elk';

        if (layoutEngine === 'elk') {
            this.elkAutoLayout();
        } else if (layoutEngine === 'dagre') {
            this.dagreAutoLayout();
        } else {
            this.colaAutoLayout();
        }
    }

    colaAutoLayout() {
        // Use WebCola for force-directed graph layout
        if (typeof cola === 'undefined') {
            console.error('WebCola library not loaded');
            this.updateInfo('Error: WebCola library not available');
            return;
        }

        // Get parameters from controls
        const linkDistance = parseInt(document.getElementById('link-distance-slider')?.value || 100);
        const iterations = parseInt(document.getElementById('iterations-slider')?.value || 50);
        // Canvas scales with object count so large patches aren't crammed.
        const canvasSize = this.getLayoutCanvasSize();
        const canvasWidth = canvasSize.width;
        const canvasHeight = canvasSize.height;
        const avoidOverlaps = document.getElementById('avoid-overlaps-checkbox')?.checked !== false;
        const constraintPreset = document.getElementById('constraint-preset')?.value || 'none';
        const flowDirection = document.getElementById('flow-direction')?.value || 'y';
        const flowSpacing = parseInt(document.getElementById('flow-spacing-slider')?.value || 50);

        // Use sensible default for convergence threshold (not exposed in UI)
        const convergenceThreshold = 1e-3;

        this.updateInfo(`Computing auto-layout (linkDistance: ${linkDistance}, iterations: ${iterations}, flow: ${flowDirection})...`);

        // Prepare nodes and links for WebCola
        const nodes = [];
        const links = [];
        const nodeMap = new Map();

        // Create nodes array
        let index = 0;
        this.boxes.forEach((box, boxId) => {
            if (this.isHidden(box)) return;  // exclude hidden comments from layout
            nodes.push({
                id: boxId,
                width: box.width || 60,
                height: box.height || 22,
                x: box.x || 0,
                y: box.y || 0,
                fixed: 0  // Not fixed
            });
            nodeMap.set(boxId, index++);
        });

        // Create links array from patchlines
        this.lines.forEach(line => {
            const sourceIdx = nodeMap.get(line.src);
            const targetIdx = nodeMap.get(line.dst);
            if (sourceIdx !== undefined && targetIdx !== undefined) {
                links.push({
                    source: sourceIdx,
                    target: targetIdx,
                    length: linkDistance  // Use slider value
                });
            }
        });

        // Generate constraints based on preset
        const constraints = this.generateConstraints(nodes, constraintPreset);

        // Configure WebCola using d3adaptor with parameters from sliders
        const layout = cola.d3adaptor(d3)
            .convergenceThreshold(convergenceThreshold)
            .size([canvasWidth, canvasHeight])
            .nodes(nodes)
            .links(links)
            .avoidOverlaps(avoidOverlaps)
            .handleDisconnected(true)
            .jaccardLinkLengths(linkDistance);

        // Determine if flow direction is reversed
        const isReversed = flowDirection.endsWith('-reverse');
        const baseFlowAxis = isReversed ? flowDirection.replace('-reverse', '') : flowDirection;

        // Apply flow layout if direction is specified
        if (flowDirection !== 'none') {
            layout.flowLayout(baseFlowAxis, flowSpacing);
        }

        // Apply constraints if any
        if (constraints.length > 0) {
            layout.constraints(constraints);
        }

        // Run the layout algorithm with custom iteration count
        layout.start(iterations, iterations, iterations);

        // If flow direction is reversed, flip the coordinates
        if (isReversed) {
            this.reverseFlowLayout(nodes, baseFlowAxis, canvasWidth, canvasHeight);
        }

        // Update box positions with smooth SVG.js animations
        // Also animate patchlines for smooth transitions

        const animationDuration = 500;
        const animationPromises = [];

        // Store old positions for patchline animation
        const oldPositions = new Map();
        nodes.forEach(node => {
            const box = this.boxes.get(node.id);
            if (box) {
                oldPositions.set(node.id, { x: box.x || 0, y: box.y || 0 });
            }
        });

        // Update internal state first
        nodes.forEach(node => {
            const box = this.boxes.get(node.id);
            if (box) {
                box.x = Math.round(node.x);
                box.y = Math.round(node.y);
            }
        });

        // Center the layout
        this.centerLayout();

        // Animate boxes
        nodes.forEach(node => {
            const box = this.boxes.get(node.id);
            const oldPos = oldPositions.get(node.id);
            if (box && oldPos) {
                const newX = box.x;
                const newY = box.y;

                // Find the SVG element for this box
                const boxElement = this.boxesGroup.querySelector(`[data-id="${node.id}"]`);

                if (boxElement) {
                    const svgElement = SVG(boxElement);
                    const deltaX = newX - oldPos.x;
                    const deltaY = newY - oldPos.y;

                    // Only animate if there's actual movement
                    if (Math.abs(deltaX) > 1 || Math.abs(deltaY) > 1) {
                        const promise = new Promise(resolve => {
                            svgElement.animate(animationDuration, 0, 'now')
                                .ease('<>')
                                .transform({ translateX: deltaX, translateY: deltaY })
                                .after(() => resolve());
                        });
                        animationPromises.push(promise);
                    }
                }

                // Send position update to server
                this.sendMessage({
                    type: 'update_position',
                    box_id: node.id,
                    x: newX,
                    y: newY
                });
            }
        });

        // Animate patchlines by updating line coordinates during animation
        this.animatePatchlines(oldPositions, animationDuration);

        // Wait for animations to complete, then re-render to finalize positions
        Promise.all(animationPromises).then(() => {
            console.log('Animations complete, re-rendering...');
            this.render();
            const constraintInfo = constraints.length > 0 ? `, ${constraints.length} constraints` : '';
            const flowInfo = flowDirection !== 'none' ? `, flow: ${flowDirection} (${flowSpacing}px)` : '';
            this.updateInfo(`Auto-layout applied: ${nodes.length} objects, linkDistance: ${linkDistance}, iterations: ${iterations}${flowInfo}${constraintInfo}`);
        });
    }

    animatePatchlines(oldPositions, duration) {
        /**
         * Animate patchlines from old positions to new positions.
         * Uses requestAnimationFrame for smooth interpolation.
         */
        const startTime = performance.now();

        const animate = (currentTime) => {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);

            // Ease in-out function
            const eased = progress < 0.5
                ? 2 * progress * progress
                : 1 - Math.pow(-2 * progress + 2, 2) / 2;

            // Update each patchline
            this.lines.forEach(line => {
                const srcBox = this.boxes.get(line.src);
                const dstBox = this.boxes.get(line.dst);
                const srcOld = oldPositions.get(line.src);
                const dstOld = oldPositions.get(line.dst);

                if (srcBox && dstBox && srcOld && dstOld) {
                    // Interpolate positions
                    const srcX = srcOld.x + (srcBox.x - srcOld.x) * eased;
                    const srcY = srcOld.y + (srcBox.y - srcOld.y) * eased;
                    const dstX = dstOld.x + (dstBox.x - dstOld.x) * eased;
                    const dstY = dstOld.y + (dstBox.y - dstOld.y) * eased;

                    // Calculate port positions with interpolated box positions
                    const srcPoint = this.getPortPositionFromCoords(
                        srcX, srcY, srcBox.width || 60, srcBox.height || 22,
                        line.src_outlet || 0, srcBox.outlet_count || 1, true
                    );
                    const dstPoint = this.getPortPositionFromCoords(
                        dstX, dstY, dstBox.width || 60, dstBox.height || 22,
                        line.dst_inlet || 0, dstBox.inlet_count || 1, false
                    );

                    // Find and update the line element
                    const lineGroup = this.linesGroup.querySelector(
                        `[data-src="${line.src}"][data-dst="${line.dst}"][data-src-outlet="${line.src_outlet || 0}"][data-dst-inlet="${line.dst_inlet || 0}"]`
                    );

                    if (lineGroup) {
                        const visibleLine = lineGroup.querySelector('.patchline');
                        const hitbox = lineGroup.querySelector('.patchline-hitbox');

                        if (visibleLine) {
                            visibleLine.setAttribute('x1', srcPoint.x);
                            visibleLine.setAttribute('y1', srcPoint.y);
                            visibleLine.setAttribute('x2', dstPoint.x);
                            visibleLine.setAttribute('y2', dstPoint.y);
                        }
                        if (hitbox) {
                            hitbox.setAttribute('x1', srcPoint.x);
                            hitbox.setAttribute('y1', srcPoint.y);
                            hitbox.setAttribute('x2', dstPoint.x);
                            hitbox.setAttribute('y2', dstPoint.y);
                        }
                    }
                }
            });

            if (progress < 1) {
                requestAnimationFrame(animate);
            }
        };

        requestAnimationFrame(animate);
    }

    getPortPositionFromCoords(x, y, w, h, portIndex, portCount, isOutlet) {
        /**
         * Calculate port position given box coordinates (for animation interpolation).
         */
        const count = Math.max(1, portCount);
        const safeIndex = Math.max(0, Math.min(portIndex, count - 1));
        const spacing = w / (count + 1);

        return {
            x: x + spacing * (safeIndex + 1),
            y: isOutlet ? (y + h) : y
        };
    }

    elkAutoLayout() {
        // Use ELK for hierarchical graph layout with port support
        if (typeof ELK === 'undefined') {
            console.error('ELK library not loaded');
            this.updateInfo('Error: ELK library not available');
            return;
        }

        this.updateInfo('Computing ELK layout with ports...');

        // Get parameters from controls
        const flowDirection = document.getElementById('flow-direction')?.value || 'y';
        const flowSpacing = parseInt(document.getElementById('flow-spacing-slider')?.value || 50);
        const elkAlgorithm = document.getElementById('elk-algorithm')?.value || 'layered';
        const elkNodePlacement = document.getElementById('elk-node-placement')?.value || 'NETWORK_SIMPLEX';
        const elkEdgeRouting = document.getElementById('elk-edge-routing')?.value || 'ORTHOGONAL';

        // Determine ELK direction from flow direction
        let elkDirection = 'RIGHT';  // default: left-to-right
        if (flowDirection === 'y' || flowDirection === 'vertical-flow') {
            elkDirection = 'DOWN';  // top-to-bottom
        } else if (flowDirection === 'x-reverse') {
            elkDirection = 'LEFT';  // right-to-left
        } else if (flowDirection === 'y-reverse') {
            elkDirection = 'UP';  // bottom-to-top
        }

        // Build ELK graph structure with ports
        const children = [];
        const edges = [];
        const nodeMap = new Map();

        // First, analyze connections to determine required ports
        const requiredOutlets = new Map();  // boxId -> max outlet index needed
        const requiredInlets = new Map();   // boxId -> max inlet index needed

        this.lines.forEach(line => {
            // Skip connections where source or destination box doesn't exist
            if (!this.boxes.has(line.src) || !this.boxes.has(line.dst)) {
                return;
            }
            // Skip connections touching a hidden comment.
            if (this.isHidden(this.boxes.get(line.src)) || this.isHidden(this.boxes.get(line.dst))) {
                return;
            }

            const srcOutlet = line.src_outlet || 0;
            const dstInlet = line.dst_inlet || 0;

            const currentMaxOutlet = requiredOutlets.get(line.src) || 0;
            requiredOutlets.set(line.src, Math.max(currentMaxOutlet, srcOutlet + 1));

            const currentMaxInlet = requiredInlets.get(line.dst) || 0;
            requiredInlets.set(line.dst, Math.max(currentMaxInlet, dstInlet + 1));
        });

        // Create nodes with ports
        this.boxes.forEach((box, boxId) => {
            if (this.isHidden(box)) return;  // exclude hidden comments from layout

            const width = box.width || 60;
            const height = box.height || 22;

            // Create port definitions
            const ports = [];

            // Add inlet ports (top of box)
            // Use the greater of: declared inlet_count OR required by connections
            const declaredInlets = box.inlet_count || 0;
            const neededInlets = requiredInlets.get(boxId) || 0;
            const inletCount = Math.max(declaredInlets, neededInlets);

            for (let i = 0; i < inletCount; i++) {
                ports.push({
                    id: `${boxId}_inlet_${i}`,
                    properties: {
                        'port.side': 'NORTH',
                        'port.index': i
                    }
                });
            }

            // Add outlet ports (bottom of box)
            // Use the greater of: declared outlet_count OR required by connections
            const declaredOutlets = box.outlet_count || 0;
            const neededOutlets = requiredOutlets.get(boxId) || 0;
            const outletCount = Math.max(declaredOutlets, neededOutlets);

            for (let i = 0; i < outletCount; i++) {
                ports.push({
                    id: `${boxId}_outlet_${i}`,
                    properties: {
                        'port.side': 'SOUTH',
                        'port.index': i
                    }
                });
            }

            children.push({
                id: boxId,
                width: width,
                height: height,
                ports: ports
            });

            nodeMap.set(boxId, box);
        });

        // Create edges with port connections (only for valid boxes)
        this.lines.forEach(line => {
            // Skip edges where source or destination box doesn't exist
            if (!this.boxes.has(line.src) || !this.boxes.has(line.dst)) {
                console.warn(`Skipping edge: source ${line.src} or destination ${line.dst} not found`);
                return;
            }
            // Skip edges touching a hidden comment (its node isn't in the graph).
            if (this.isHidden(this.boxes.get(line.src)) || this.isHidden(this.boxes.get(line.dst))) {
                return;
            }

            const srcOutlet = line.src_outlet || 0;
            const dstInlet = line.dst_inlet || 0;

            edges.push({
                id: `${line.src}_${srcOutlet}_to_${line.dst}_${dstInlet}`,
                sources: [`${line.src}_outlet_${srcOutlet}`],
                targets: [`${line.dst}_inlet_${dstInlet}`]
            });
        });

        // Build ELK graph with dynamic options
        const layoutOptions = {
            'elk.algorithm': elkAlgorithm,
            'elk.direction': elkDirection,
            'elk.spacing.nodeNode': flowSpacing.toString(),
            'elk.portConstraints': 'FIXED_SIDE'
        };

        // Add algorithm-specific options
        if (elkAlgorithm === 'layered') {
            layoutOptions['elk.layered.spacing.nodeNodeBetweenLayers'] = flowSpacing.toString();
            layoutOptions['elk.layered.nodePlacement.strategy'] = elkNodePlacement;
            layoutOptions['elk.edgeRouting'] = elkEdgeRouting;
        }

        const graph = {
            id: "root",
            layoutOptions: layoutOptions,
            children: children,
            edges: edges
        };

        console.log('ELK graph:', graph);

        // Run ELK layout
        const elk = new ELK();
        elk.layout(graph)
            .then(layoutedGraph => {
                console.log('ELK layout result:', layoutedGraph);

                // Store old positions for animation
                const oldPositions = new Map();
                this.boxes.forEach((box, boxId) => {
                    oldPositions.set(boxId, { x: box.x || 0, y: box.y || 0 });
                });

                // First pass: update all internal positions
                layoutedGraph.children.forEach(node => {
                    const box = this.boxes.get(node.id);
                    if (box) {
                        box.x = Math.round(node.x);
                        box.y = Math.round(node.y);
                    }
                });

                // Center the layout
                this.centerLayout();

                // Second pass: animate and send updates
                const animationPromises = [];
                const animationDuration = 500;

                layoutedGraph.children.forEach(node => {
                    const box = this.boxes.get(node.id);
                    const oldPos = oldPositions.get(node.id);

                    if (box && oldPos) {
                        const newX = box.x;
                        const newY = box.y;

                        // Find the SVG element for this box
                        const boxElement = this.boxesGroup.querySelector(`[data-id="${node.id}"]`);

                        if (boxElement) {
                            const svgElement = SVG(boxElement);
                            const deltaX = newX - oldPos.x;
                            const deltaY = newY - oldPos.y;

                            if (Math.abs(deltaX) > 1 || Math.abs(deltaY) > 1) {
                                const promise = new Promise(resolve => {
                                    svgElement.animate(animationDuration, 0, 'now')
                                        .ease('<>')
                                        .transform({ translateX: deltaX, translateY: deltaY })
                                        .after(() => resolve());
                                });
                                animationPromises.push(promise);
                            }
                        }

                        // Send position update to server
                        this.sendMessage({
                            type: 'update_position',
                            box_id: node.id,
                            x: newX,
                            y: newY
                        });
                    }
                });

                // Animate patchlines
                this.animatePatchlines(oldPositions, animationDuration);

                // Wait for animations to complete, then re-render
                Promise.all(animationPromises).then(() => {
                    console.log('ELK animations complete, re-rendering...');
                    this.render();
                    this.updateInfo(`ELK layout applied: ${layoutedGraph.children.length} objects, algorithm: ${elkAlgorithm}, direction: ${elkDirection}, spacing: ${flowSpacing}px`);
                });
            })
            .catch(error => {
                console.error('ELK layout error:', error);
                this.updateInfo(`ELK layout error: ${error.message}`);
            });
    }

    dagreAutoLayout() {
        // Use Dagre for hierarchical DAG layout
        if (typeof dagre === 'undefined') {
            console.error('Dagre library not loaded');
            this.updateInfo('Error: Dagre library not available');
            return;
        }

        this.updateInfo('Computing Dagre layout...');

        // Get parameters from controls
        const flowDirection = document.getElementById('flow-direction')?.value || 'y';
        const flowSpacing = parseInt(document.getElementById('flow-spacing-slider')?.value || 50);
        const dagreRanker = document.getElementById('dagre-ranker')?.value || 'network-simplex';
        const dagreAlign = document.getElementById('dagre-align')?.value || '';

        // Determine dagre rankdir from flow direction
        let rankdir = 'TB';  // default: top-to-bottom
        if (flowDirection === 'x' || flowDirection === 'horizontal-flow') {
            rankdir = 'LR';  // left-to-right
        } else if (flowDirection === 'x-reverse') {
            rankdir = 'RL';  // right-to-left
        } else if (flowDirection === 'y-reverse') {
            rankdir = 'BT';  // bottom-to-top
        }

        // Create a new directed graph
        const g = new dagre.graphlib.Graph();

        // Set graph options
        const graphOptions = {
            rankdir: rankdir,
            nodesep: flowSpacing * 0.5,  // horizontal separation
            ranksep: flowSpacing,         // vertical separation between ranks
            marginx: 20,
            marginy: 20,
            ranker: dagreRanker
        };

        if (dagreAlign) {
            graphOptions.align = dagreAlign;
        }

        g.setGraph(graphOptions);

        // Default edge label (required by dagre)
        g.setDefaultEdgeLabel(() => ({}));

        // Add nodes
        this.boxes.forEach((box, boxId) => {
            if (this.isHidden(box)) return;  // exclude hidden comments from layout

            const width = box.width || 60;
            const height = box.height || 22;

            g.setNode(boxId, {
                width: width,
                height: height,
                label: box.text || boxId
            });
        });

        // Add edges (only for valid boxes)
        this.lines.forEach(line => {
            if (!this.boxes.has(line.src) || !this.boxes.has(line.dst)) {
                console.warn(`Skipping edge: source ${line.src} or destination ${line.dst} not found`);
                return;
            }
            // Skip edges touching a hidden comment (its node isn't in the graph).
            if (this.isHidden(this.boxes.get(line.src)) || this.isHidden(this.boxes.get(line.dst))) {
                return;
            }
            g.setEdge(line.src, line.dst);
        });

        // Run the layout algorithm
        try {
            dagre.layout(g);
        } catch (error) {
            console.error('Dagre layout error:', error);
            this.updateInfo(`Dagre layout error: ${error.message}`);
            return;
        }

        // Store old positions for animation
        const oldPositions = new Map();
        this.boxes.forEach((box, boxId) => {
            oldPositions.set(boxId, { x: box.x || 0, y: box.y || 0 });
        });

        // First pass: update all internal positions
        g.nodes().forEach(nodeId => {
            const node = g.node(nodeId);
            const box = this.boxes.get(nodeId);

            if (box && node) {
                // Dagre returns center coordinates, convert to top-left
                box.x = Math.round(node.x - node.width / 2);
                box.y = Math.round(node.y - node.height / 2);
            }
        });

        // Center the layout
        this.centerLayout();

        // Second pass: animate and send updates
        const animationDuration = 500;
        const animationPromises = [];

        g.nodes().forEach(nodeId => {
            const box = this.boxes.get(nodeId);
            const oldPos = oldPositions.get(nodeId);

            if (box && oldPos) {
                const newX = box.x;
                const newY = box.y;

                // Find the SVG element for this box
                const boxElement = this.boxesGroup.querySelector(`[data-id="${nodeId}"]`);

                if (boxElement) {
                    const svgElement = SVG(boxElement);
                    const deltaX = newX - oldPos.x;
                    const deltaY = newY - oldPos.y;

                    // Only animate if there's actual movement
                    if (Math.abs(deltaX) > 1 || Math.abs(deltaY) > 1) {
                        const promise = new Promise(resolve => {
                            svgElement.animate(animationDuration, 0, 'now')
                                .ease('<>')
                                .transform({ translateX: deltaX, translateY: deltaY })
                                .after(() => resolve());
                        });
                        animationPromises.push(promise);
                    }
                }

                // Send position update to server
                this.sendMessage({
                    type: 'update_position',
                    box_id: nodeId,
                    x: newX,
                    y: newY
                });
            }
        });

        // Animate patchlines
        this.animatePatchlines(oldPositions, animationDuration);

        // Wait for animations to complete, then re-render
        Promise.all(animationPromises).then(() => {
            console.log('Dagre animations complete, re-rendering...');
            this.render();
            this.updateInfo(`Dagre layout applied: ${g.nodeCount()} objects, direction: ${rankdir}, ranker: ${dagreRanker}, spacing: ${flowSpacing}px`);
        });
    }

    // Helper methods

    getLayoutCanvasSize() {
        /**
         * Canvas dimensions for layout/centering, scaled to the object count.
         * The compact slider defaults suit small patches, but a fixed small
         * canvas crams a large patch (force-directed especially) into a blob.
         * Returns at least the slider values, growing with node count so complex
         * patches get room to breathe.
         */
        const sliderW = parseInt(document.getElementById('canvas-width-slider')?.value || 400);
        const sliderH = parseInt(document.getElementById('canvas-height-slider')?.value || 300);
        const spacing = parseInt(document.getElementById('flow-spacing-slider')?.value || 50);

        // Average footprint over the boxes that actually get laid out.
        let n = 0, avgW = 0, avgH = 0;
        this.boxes.forEach(b => {
            if (this.isHidden(b)) return;
            n += 1;
            avgW += (b.width || 60);
            avgH += (b.height || 22);
        });
        if (n === 0) { n = 1; avgW = 60; avgH = 22; }
        avgW = avgW / n;
        avgH = avgH / n;

        // Room for ~sqrt(n) nodes per side, each plus spacing between them.
        const perSide = Math.ceil(Math.sqrt(n));
        const needW = Math.round(perSide * (avgW + spacing));
        const needH = Math.round(perSide * (avgH + spacing));

        return {
            width: Math.max(sliderW, needW),
            height: Math.max(sliderH, needH)
        };
    }

    centerLayout() {
        /**
         * Center all boxes within the canvas.
         * Call this after computing layout positions but before animation.
         */
        if (this.boxes.size === 0) return;

        // Canvas dimensions, scaled to the object count (see getLayoutCanvasSize).
        const canvasSize = this.getLayoutCanvasSize();
        const canvasWidth = canvasSize.width;
        const canvasHeight = canvasSize.height;

        // Calculate bounding box of all objects
        let minX = Infinity, minY = Infinity;
        let maxX = -Infinity, maxY = -Infinity;

        this.boxes.forEach(box => {
            if (this.isHidden(box)) return;  // center on the visible (laid-out) objects

            const x = box.x || 0;
            const y = box.y || 0;
            const w = box.width || 60;
            const h = box.height || 22;

            minX = Math.min(minX, x);
            minY = Math.min(minY, y);
            maxX = Math.max(maxX, x + w);
            maxY = Math.max(maxY, y + h);
        });

        if (maxX === -Infinity) return;  // nothing visible to center

        // Calculate content dimensions
        const contentWidth = maxX - minX;
        const contentHeight = maxY - minY;

        // Calculate offset to center content
        const offsetX = (canvasWidth - contentWidth) / 2 - minX;
        const offsetY = (canvasHeight - contentHeight) / 2 - minY;

        // Apply offset to all boxes
        this.boxes.forEach(box => {
            box.x = (box.x || 0) + offsetX;
            box.y = (box.y || 0) + offsetY;
        });

        console.log(`Layout centered: offset (${offsetX.toFixed(1)}, ${offsetY.toFixed(1)})`);
    }

    toggleLayoutParameters(engine) {
        // Remove all layout engine classes
        document.body.classList.remove('layout-engine-cola', 'layout-engine-elk', 'layout-engine-dagre');

        // Add the appropriate class for the selected engine
        document.body.classList.add(`layout-engine-${engine}`);

        // Also keep the data attribute for backwards compatibility
        document.body.setAttribute('data-layout-engine', engine);

        console.log(`Layout engine changed to: ${engine}`);
        console.log(`Body classes: ${document.body.className}`);

        // DIRECT JavaScript manipulation for Safari compatibility
        const colaParams = document.querySelectorAll('.param-cola-only');
        const elkParams = document.querySelectorAll('.param-elk-only');
        const dagreParams = document.querySelectorAll('.param-dagre-only');

        // Hide all engine-specific params first
        [colaParams, elkParams, dagreParams].forEach(params => {
            params.forEach(el => {
                el.style.display = 'none';
                el.style.visibility = 'hidden';
                el.style.height = '0';
                el.style.overflow = 'hidden';
            });
        });

        // Show params for the selected engine
        let activeParams;
        if (engine === 'elk') {
            activeParams = elkParams;
        } else if (engine === 'dagre') {
            activeParams = dagreParams;
        } else {
            activeParams = colaParams;
        }

        activeParams.forEach(el => {
            el.style.display = 'flex';
            el.style.visibility = 'visible';
            el.style.height = 'auto';
            el.style.overflow = 'visible';
        });

        // Debug: count visible parameters
        const visibleCola = Array.from(colaParams).filter(el => el.offsetHeight > 0).length;
        const visibleElk = Array.from(elkParams).filter(el => el.offsetHeight > 0).length;
        console.log(`Visible cola params: ${visibleCola}/${colaParams.length}, elk params: ${visibleElk}/${elkParams.length}`);
    }

    getSVGPoint(event) {
        const CTM = this.svg.getScreenCTM();
        return {
            x: (event.clientX - CTM.e) / CTM.a,
            y: (event.clientY - CTM.f) / CTM.d
        };
    }

    getViewBox() {
        /** Current viewBox as {x, y, w, h}, parsed from the attribute. */
        const vbStr = this.svg.getAttribute('viewBox') || '0 0 1200 800';
        const [x, y, w, h] = vbStr.split(/[\s,]+/).map(Number);
        return { x, y, w, h };
    }

    setViewBox(x, y, w, h) {
        this.svg.setAttribute('viewBox', `${x} ${y} ${w} ${h}`);
    }

    startPan(event) {
        this.panning = true;
        this.panStart = {
            clientX: event.clientX,
            clientY: event.clientY,
            vb: this.getViewBox()
        };
        this.svg.style.cursor = 'grabbing';
    }

    endPan() {
        if (!this.panning) return;
        this.panning = false;
        this.panStart = null;
        // Return to the grab cursor if Space is still held, else the default.
        this.svg.style.cursor = this.spaceDown ? 'grab' : '';
    }

    handleWheel(event) {
        /**
         * Zoom the viewBox toward the cursor. deltaY>0 (scroll down / pinch out)
         * zooms out by enlarging the viewBox; the point under the cursor stays
         * fixed. Width is clamped to keep the view usable.
         */
        event.preventDefault();
        const MIN_W = 40;       // most zoomed-in (px of content across the view)
        const MAX_W = 200000;   // most zoomed-out

        const vb = this.getViewBox();
        const pt = this.getSVGPoint(event);  // cursor in SVG coords (old viewBox)

        // Exponential zoom for smooth trackpad/wheel behaviour.
        const factor = Math.exp(event.deltaY * 0.001);
        let newW = Math.max(MIN_W, Math.min(MAX_W, vb.w * factor));
        const scale = newW / vb.w;
        const newH = vb.h * scale;

        // Keep the cursor's content point at the same screen position.
        const fracX = (pt.x - vb.x) / vb.w;
        const fracY = (pt.y - vb.y) / vb.h;
        const nx = pt.x - fracX * newW;
        const ny = pt.y - fracY * newH;

        this.setViewBox(nx, ny, newW, newH);
        this.userAdjustedView = true;
    }

    fitView() {
        /** Re-engage auto-fit: frame the whole (visible) patch. Bound to "f". */
        this.userAdjustedView = false;
        this.updateViewBox();
        this.updateInfo('Fit to view');
    }

    undo() {
        this.sendMessage({ type: 'undo' });
        this.updateInfo('Undo');
    }

    redo() {
        this.sendMessage({ type: 'redo' });
        this.updateInfo('Redo');
    }

    createObjectDialog(x, y) {
        /**
         * Open the create-object modal (replaces window.prompt). Remembers the
         * canvas position so the object lands where the user double-clicked, and
         * offers a typeahead of common Max objects.
         */
        const modal = document.getElementById('create-modal');
        const input = document.getElementById('create-modal-input');
        if (!modal || !input) return;

        this._createPos = { x: x || 100, y: y || 100 };
        input.value = '';
        modal.hidden = false;
        input.focus();
    }

    isCreateModalOpen() {
        const modal = document.getElementById('create-modal');
        return !!modal && !modal.hidden;
    }

    closeCreateModal() {
        const modal = document.getElementById('create-modal');
        if (modal) modal.hidden = true;
    }

    commitCreateModal() {
        const input = document.getElementById('create-modal-input');
        const text = (input?.value || '').trim();
        if (text) {
            const pos = this._createPos || { x: 100, y: 100 };
            this.sendMessage({
                type: 'create_object',
                text: text,
                x: pos.x,
                y: pos.y
            });
            this.updateInfo(`Created ${text}`);
        }
        this.closeCreateModal();
    }

    initializeCreateModal() {
        // Populate the typeahead with common Max objects.
        const datalist = document.getElementById('max-object-list');
        if (datalist) {
            COMMON_MAX_OBJECTS.forEach(name => {
                const opt = document.createElement('option');
                opt.value = name;
                datalist.appendChild(opt);
            });
        }

        const ok = document.getElementById('create-modal-ok');
        if (ok) ok.addEventListener('click', () => this.commitCreateModal());
        const close = document.getElementById('create-modal-close');
        if (close) close.addEventListener('click', () => this.closeCreateModal());
        const backdrop = document.getElementById('create-modal-backdrop');
        if (backdrop) backdrop.addEventListener('click', () => this.closeCreateModal());

        const input = document.getElementById('create-modal-input');
        if (input) {
            input.addEventListener('keydown', (e) => {
                // Keep the global editor shortcuts from firing while typing here.
                e.stopPropagation();
                if (e.key === 'Enter') {
                    e.preventDefault();
                    this.commitCreateModal();
                } else if (e.key === 'Escape') {
                    e.preventDefault();
                    this.closeCreateModal();
                }
            });
        }
    }

    isFilePickerOpen() {
        const modal = document.getElementById('file-picker');
        return !!modal && !modal.hidden;
    }

    openFilePicker() {
        /** Open the server-side file-picker modal and request a listing. */
        const modal = document.getElementById('file-picker');
        if (!modal) return;
        modal.hidden = false;
        const status = document.getElementById('file-picker-status');
        if (status) status.textContent = 'Loading...';
        // No directory -> the server defaults to the current patch's folder.
        this.requestPatchList();
    }

    closeFilePicker() {
        const modal = document.getElementById('file-picker');
        if (modal) modal.hidden = true;
    }

    requestPatchList(directory) {
        this.sendMessage({
            type: 'list_patches',
            directory: directory || ''
        });
    }

    renderFileList(data) {
        /**
         * Populate the file-picker modal from a patch_list message. Directories
         * navigate deeper on click; patch files open on the server (preserving the
         * real path for save-back). Text is set via textContent so filesystem
         * names cannot inject markup.
         */
        const pathEl = document.getElementById('file-picker-path');
        if (pathEl) pathEl.textContent = data.directory || '';

        const list = document.getElementById('file-picker-list');
        if (!list) return;
        list.textContent = '';

        // "Up" entry to the parent directory, when not at the filesystem root.
        if (data.parent) {
            const up = document.createElement('li');
            up.className = 'file-entry file-dir';
            up.textContent = '.. (up)';
            up.addEventListener('click', () => this.requestPatchList(data.parent));
            list.appendChild(up);
        }

        const entries = data.entries || [];
        if (entries.length === 0 && !data.parent) {
            const empty = document.createElement('li');
            empty.className = 'file-empty';
            empty.textContent = 'No patch files or folders here.';
            list.appendChild(empty);
        }

        entries.forEach(entry => {
            const li = document.createElement('li');
            li.className = 'file-entry ' + (entry.is_dir ? 'file-dir' : 'file-patch');
            li.textContent = (entry.is_dir ? '📁 ' : '📄 ') + entry.name;
            li.title = entry.path;
            if (entry.is_dir) {
                li.addEventListener('click', () => this.requestPatchList(entry.path));
            } else {
                li.addEventListener('click', () => this.openServerFile(entry.path));
            }
            list.appendChild(li);
        });

        const status = document.getElementById('file-picker-status');
        if (status) {
            const fileCount = entries.filter(e => !e.is_dir).length;
            status.textContent = `${fileCount} patch file(s)`;
        }
    }

    openServerFile(path) {
        /** Open a patch by its real server-side path and close the picker. */
        // Discard layout/selection state tied to the previous patch so Reset
        // Layout does not restore positions from a different file.
        this.originalPositions.clear();
        this.selectedBox = null;
        this.selectedBoxes.clear();
        this.selectedLine = null;
        // Force auto-fit for the newly opened patch even if it shares a title.
        this.userAdjustedView = false;
        this._viewPatcherKey = null;

        this.sendMessage({ type: 'open', filepath: path });
        this.updateInfo(`Opening ${path}...`);
        this.closeFilePicker();
    }

    async handleFileSelected(event) {
        /**
         * Read a patch file chosen via the native file picker and send its text
         * to the server. A .maxpat is JSON, so the server parses the contents
         * (the browser cannot expose the file's real path for a server-side open).
         *
         * Progress is echoed to the info bar so failures are visible without the
         * dev console. Uses Blob.text() (well-supported in modern Safari) rather
         * than FileReader.
         */
        const input = event.target;
        const file = input.files && input.files[0];
        if (!file) {
            this.updateInfo('No file selected');
            return;
        }

        this.updateInfo(`Reading ${file.name}...`);
        try {
            const content = await file.text();
            this.updateInfo(`Opening ${file.name}...`);

            // Discard layout/selection state tied to the previous patch so
            // Reset Layout does not restore positions from a different file.
            this.originalPositions.clear();
            this.selectedBox = null;
            this.selectedBoxes.clear();
            this.selectedLine = null;
            // Force auto-fit for the newly opened patch even if it shares a title.
            this.userAdjustedView = false;
            this._viewPatcherKey = null;

            this.sendMessage({
                type: 'open_content',
                filename: file.name,
                content: content
            });
            this.closeFilePicker();  // Close the modal if the upload fallback was used
        } catch (err) {
            console.error('Could not read file:', err);
            this.updateInfo(`Could not read ${file.name}: ${err && err.message}`);
        } finally {
            // Reset after reading so re-picking the same file re-fires 'change'.
            input.value = '';
        }
    }

    suggestedFilename() {
        /** Best-effort .maxpat filename from the current file path or title. */
        if (this.currentFilepath) {
            const base = this.currentFilepath.split(/[\\/]/).pop();
            if (base) return base;
        }
        const titleEl = document.getElementById('title');
        const title = (titleEl?.textContent || '')
            .replace('py2max Interactive Editor - ', '')
            .trim() || 'patch';
        return title.endsWith('.maxpat') ? title : `${title}.maxpat`;
    }

    async handleSave() {
        /**
         * Save As via a native file dialog. The server writes to its own disk by
         * path, which a browser can't choose, so Save As serializes the patch and
         * lets the browser write it: the File System Access API (Chrome) shows the
         * real OS save dialog; other browsers fall back to a download.
         *
         * The save-file picker must run inside the click gesture, so acquire the
         * file handle now and write to it once the server returns the content.
         */
        this._saveHandle = null;
        if (window.showSaveFilePicker) {
            try {
                this._saveHandle = await window.showSaveFilePicker({
                    suggestedName: this.suggestedFilename(),
                    types: [{
                        description: 'Max patch',
                        accept: { 'application/json': ['.maxpat'] }
                    }]
                });
            } catch (err) {
                if (err && err.name === 'AbortError') {
                    this.updateInfo('Save cancelled');
                    return;
                }
                // Picker unavailable/failed: fall back to a plain download.
                this._saveHandle = null;
            }
        }

        this._saveFilename = this._saveHandle ? this._saveHandle.name : this.suggestedFilename();
        this.sendMessage({ type: 'export_patch' });
        this.updateInfo('Saving...');
    }

    async writePatchToFile(content) {
        /** Write serialized patch content returned by the server (see handleSave). */
        const handle = this._saveHandle;
        this._saveHandle = null;

        if (handle) {
            try {
                const writable = await handle.createWritable();
                await writable.write(content);
                await writable.close();
                this.updateInfo(`Saved ${handle.name}`);
                return;
            } catch (err) {
                console.error('Write failed, falling back to download:', err);
            }
        }

        // Fallback: trigger a browser download.
        const filename = this._saveFilename || 'patch.maxpat';
        const blob = new Blob([content], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        this.updateInfo(`Downloaded ${filename}`);
    }

    showSaveAsDialog() {
        // Retained for the server's save_as_required flow: route it through the
        // same native Save As path rather than a text prompt.
        this.handleSave();
    }

    sendMessage(message) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
        } else {
            console.error('WebSocket not connected');
        }
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new InteractiveEditor();
});
