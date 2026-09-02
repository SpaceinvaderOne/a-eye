// ── API Prefix (set by templates for workspace mode) ────────
var API_PREFIX = window.API_PREFIX || '/api';

// ── Tagline Quotes ──────────────────────────────────────────
var _taglineQuotes = [
    "In space, no one can hear your hard drives click.",
    "I'll be back... after this parity check.",
    "May the source be with you.",
    "To infinity and beyond your storage limits.",
    "I'm sorry Dave, I can't describe that photo.",
    "Live long and self-host.",
    "These aren't the photos you're looking for.",
    "Game over, man! Game over! ...just kidding, the array rebuilt.",
    "The truth is out there. Probably in the metadata.",
    "We're gonna need a bigger model.",
    "I see dead pixels.",
    "Phone home. Then check your server remotely.",
    "Open the pod bay doors, HAL. And the Docker socket.",
    "They mostly scan at night. Mostly.",
    "It's full of stars... and unprocessed photos.",
    "Resistance is futile. Your photos will be catalogued.",
    "Do. Or do not. There is no try. Unless Ollama is down.",
    "I find your lack of metadata disturbing.",
    "My precious... metadata. We wants it, we needs it.",
    "Teaching machines to see, one photo at a time.",
    "Somewhere, a vision model is squinting at your photos.",
    "No photos were harmed in the making of this filename.",
    "Giving your photos names they deserve.",
    "Currently arguing with a vision model about what a dog looks like.",
    "Renaming IMG_20210421_122426.jpg since 2026.",
    "Your photos called. They want proper names."
];

function _randomQuote() {
    return _taglineQuotes[Math.floor(Math.random() * _taglineQuotes.length)];
}

// ── HTML Escape ────────────────────────────────────────────

function _esc(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ── Auth: Logout + 401 redirect ─────────────────────────────

function doLogout(e) {
    if (e) e.preventDefault();
    fetch('/api/logout', {method: 'POST'}).finally(function() {
        window.location.href = '/login';
    });
}

// Intercept 401 responses globally — redirect to login on session expiry
(function() {
    var _origFetch = window.fetch;
    window.fetch = function() {
        return _origFetch.apply(this, arguments).then(function(resp) {
            if (resp.status === 401 && window.location.pathname !== '/login') {
                window.location.href = '/login';
            }
            return resp;
        });
    };
})();

// ── Toast Notifications ─────────────────────────────────────

function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

// ── Confirm Modal ──────────────────────────────────────────

var _confirmCallback = null;

function showConfirm(title, message, okLabel, callback, okBtnClass) {
    document.getElementById('confirm-title').textContent = title;
    document.getElementById('confirm-message').textContent = message;
    var okBtn = document.getElementById('confirm-ok-btn');
    okBtn.textContent = okLabel || 'Delete';
    okBtn.className = 'btn ' + (okBtnClass || 'btn-error');
    _confirmCallback = callback;
    document.getElementById('confirm-modal').style.display = '';
}

function confirmOk() {
    document.getElementById('confirm-modal').style.display = 'none';
    if (_confirmCallback) _confirmCallback();
    _confirmCallback = null;
}

function confirmCancel() {
    document.getElementById('confirm-modal').style.display = 'none';
    _confirmCallback = null;
}

// ── HTMX Event Handlers ────────────────────────────────────

// Show toast on successful HTMX requests
document.body.addEventListener('htmx:afterRequest', function(evt) {
    if (evt.detail.failed) {
        showToast('Request failed', 'error');
    }
});

// Handle health check response — update the Ollama status indicator
document.body.addEventListener('htmx:afterSettle', function(evt) {
    if (evt.detail.target && evt.detail.target.id === 'ollama-status') {
        try {
            const data = JSON.parse(evt.detail.xhr.responseText);
            const dot = data.ollama && data.ollama.connected ? 'connected' : 'disconnected';
            const label = data.ollama && data.ollama.connected ? 'Ollama Connected' : 'Ollama Disconnected';
            evt.detail.target.innerHTML = `<span class="status-dot ${dot}"></span> ${label}`;
        } catch (e) {
            // If it's not JSON, leave it as-is
        }
    }
});

// ── Dashboard: Live Updates (no-flicker polling) ─────────────

function _setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function updateDashboard() {
    fetch('/api/dashboard/status')
        .then(r => r.json())
        .then(data => {
            const stats = data.stats || {};
            const outcomes = data.outcomes || {};

            // Top row
            _setText('stat-total', stats.total || 0);
            _setText('stat-processed', outcomes.processed || 0);
            _setText('stat-errors', stats.error || 0);

            // Outcome row — adapt to catalogue mode
            if (window.CATALOGUE_MODE) {
                _setText('stat-catalogued', outcomes.catalogued || 0);
            } else {
                _setText('stat-renamed', outcomes.renamed || 0);
            }
            _setText('stat-descriptions', outcomes.descriptions_written || 0);
            _setText('stat-tags', outcomes.tags_written || 0);

            // Review banner (hidden in catalogue mode)
            var proposed = stats.proposed || 0;
            var banner = document.getElementById('review-banner');
            if (banner) {
                banner.style.display = (!window.CATALOGUE_MODE && proposed > 0) ? '' : 'none';
                _setText('review-count', proposed);
            }

            // Toggle idle/active progress sections
            const w = data.worker || {};
            const p = data.progress || {};
            var isActive = (stats.pending || 0) + (stats.processing || 0) > 0;
            var progressActive = document.getElementById('progress-active');
            var progressIdle = document.getElementById('progress-idle');
            if (progressActive) progressActive.style.display = isActive ? '' : 'none';
            if (progressIdle) progressIdle.style.display = isActive ? 'none' : '';

            // Stop / Resume button
            var stopBtn = document.getElementById('stop-processing-btn');
            if (stopBtn) {
                var stopRequested = w.stop_requested || false;
                if (!isActive) {
                    stopBtn.style.display = 'none';
                    stopBtn.disabled = false;
                    stopBtn.textContent = 'Stop Processing';
                    stopBtn.onclick = stopProcessing;
                } else if (stopRequested) {
                    stopBtn.style.display = '';
                    stopBtn.disabled = false;
                    stopBtn.textContent = 'Resume Processing';
                    stopBtn.onclick = resumeProcessing;
                } else {
                    stopBtn.style.display = '';
                    stopBtn.disabled = false;
                    stopBtn.textContent = 'Stop Processing';
                    stopBtn.onclick = stopProcessing;
                }
            }

            // Update progress bar (when active)
            const bar = document.getElementById('progress-bar');
            const text = document.getElementById('progress-text');
            if (bar) bar.style.width = p.percentage + '%';
            if (text) {
                if (p.total === 0) {
                    text.textContent = 'No images scanned yet';
                } else if (p.completed < p.total) {
                    text.textContent = p.completed + ' of ' + p.total + ' complete (' + p.percentage + '%)';
                } else {
                    text.textContent = 'All ' + p.total + ' images processed';
                }
            }

            // Update currently-processing thumbnails
            const activeContainer = document.getElementById('active-images');
            const activeCard = document.getElementById('active-card');
            if (activeContainer && activeCard) {
                const images = data.active_images || [];
                if (images.length === 0) {
                    activeCard.style.display = 'none';
                } else {
                    activeCard.style.display = '';
                    activeContainer.innerHTML = images.map(function(img) {
                        return '<div class="active-image-card">' +
                            '<img src="/api/images/' + img.id + '/thumbnail" alt="" class="active-thumb">' +
                            '<span class="active-filename">' + _esc(img.filename) + '</span>' +
                            '</div>';
                    }).join('');
                }
            }

            // Update schedule status
            updateScheduleStatus(data);

            // Update worker status
            var workerLabel = w.running ? 'Active' : 'Stopped';
            if (w.paused) workerLabel = 'Paused (scheduled)';
            _setText('worker-running', workerLabel);
            _setText('worker-in-flight', p.in_progress || 0);
            _setText('worker-pending', w.pending || 0);
            _setText('worker-processed', w.processed || 0);
            _setText('worker-errors', w.errors || 0);
            const scanSpan = document.getElementById('worker-scanning');
            if (scanSpan) {
                scanSpan.innerHTML = data.scanning ? ' &nbsp;|&nbsp; <strong>Scanning...</strong>' : '';
            }

            // Photo Showcase
            _initShowcase();
        })
        .catch(() => {}); // silently ignore network errors
}

function _applyKenBurns(imgEl, gridEl) {
    if (gridEl && gridEl.dataset.kenburns === 'true') {
        var n = Math.floor(Math.random() * 4) + 1;
        imgEl.className = imgEl.className.replace(/kenburns-\d/g, '');
        imgEl.classList.add('kenburns-' + n);
    }
}

var _showcaseSlot = 0;
var _showcaseTimer = null;
var _showcaseInit = false;

function _initShowcase() {
    var grid = document.getElementById('showcase-grid');
    if (!grid || _showcaseInit) return;
    _showcaseInit = true;

    // Click the showcase card (not images) to open fullscreen mosaic
    var section = document.getElementById('showcase-section');
    if (section) {
        section.style.cursor = 'pointer';
        section.addEventListener('click', function(e) {
            if (e.target.tagName !== 'IMG') {
                window.location.href = '/mosaic';
            }
        });
    }

    var tag = grid.dataset.tag || '';
    var interval = parseInt(grid.dataset.interval || '15', 10) * 1000;
    if (interval < 5000) interval = 5000;
    var fadeSec = parseFloat(grid.dataset.crossfade || '2');

    var url = API_PREFIX + '/images/random?count=2';
    if (tag) url += '&tag=' + encodeURIComponent(tag);
    fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var images = data.images || [];
            if (images.length === 0) {
                grid.innerHTML = '<p class="showcase-empty">' +
                    (tag ? "No photos with tag '" + _esc(tag) + "'" : 'No processed photos yet') + '</p>';
                return;
            }
            grid.innerHTML = '';
            images.forEach(function(img) {
                var tile = document.createElement('div');
                tile.className = 'showcase-tile';
                var el = document.createElement('img');
                el.src = API_PREFIX + '/images/' + img.id + '/thumbnail';
                el.dataset.imageId = img.id;
                el.onclick = function() { openImageModal(img.id); };
                el.loading = 'lazy';
                el.onerror = function() { this.style.visibility = 'hidden'; };
                el.style.transitionDuration = fadeSec + 's';
                _applyKenBurns(el, grid);
                tile.appendChild(el);
                grid.appendChild(tile);
            });
            if (images.length === 1) {
                var tile2 = document.createElement('div');
                tile2.className = 'showcase-tile';
                var clone = grid.querySelector('img').cloneNode(true);
                clone.onclick = function() { openImageModal(images[0].id); };
                clone.style.transitionDuration = fadeSec + 's';
                _applyKenBurns(clone, grid);
                tile2.appendChild(clone);
                grid.appendChild(tile2);
            }
            _showcaseTimer = setInterval(function() { _rotateShowcaseSlot(); }, interval);
        })
        .catch(function() {});
}

function _rotateShowcaseSlot() {
    var grid = document.getElementById('showcase-grid');
    if (!grid) return;
    var tiles = grid.querySelectorAll('.showcase-tile');
    if (tiles.length < 2) return;

    var tile = tiles[_showcaseSlot];
    var oldImg = tile.querySelector('img');
    var tag = grid.dataset.tag || '';
    var fadeSec = parseFloat(grid.dataset.crossfade || '2');

    var excludeIds = [];
    tiles.forEach(function(t) {
        var im = t.querySelector('img');
        if (im && im.dataset.imageId) excludeIds.push(im.dataset.imageId);
    });
    var url = API_PREFIX + '/images/random?count=1';
    if (tag) url += '&tag=' + encodeURIComponent(tag);
    if (excludeIds.length) url += '&exclude=' + excludeIds.join(',');

    fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var images = data.images || [];
            if (images.length === 0) return;
            var img = images[0];

            var newImg = document.createElement('img');
            newImg.style.opacity = '0';
            newImg.style.transitionDuration = fadeSec + 's';
            newImg.dataset.imageId = img.id;
            newImg.onclick = function() { openImageModal(img.id); };
            _applyKenBurns(newImg, grid);
            tile.appendChild(newImg);

            newImg.onload = function() {
                newImg.offsetHeight; // force reflow
                newImg.style.opacity = '1';
                if (oldImg) oldImg.style.opacity = '0';
                setTimeout(function() {
                    if (oldImg && oldImg.parentNode) oldImg.parentNode.removeChild(oldImg);
                }, (fadeSec * 1000) + 100);
            };
            newImg.src = API_PREFIX + '/images/' + img.id + '/thumbnail';
        })
        .catch(function() {});

    _showcaseSlot = _showcaseSlot === 0 ? 1 : 0;
}

// ── Fullscreen Mosaic ─────────────────────────────────────

var _mosaicTimer = null;
var _mosaicLastIdx = -1;
var _mosaicCols = 0;

function _isMosaicAdjacent(idx1, idx2) {
    if (_mosaicCols === 0 || idx1 < 0 || idx2 < 0) return false;
    var row1 = Math.floor(idx1 / _mosaicCols), col1 = idx1 % _mosaicCols;
    var row2 = Math.floor(idx2 / _mosaicCols), col2 = idx2 % _mosaicCols;
    return Math.abs(row1 - row2) <= 1 && Math.abs(col1 - col2) <= 1;
}

function initMosaic() {
    var grid = document.getElementById('mosaic-grid');
    if (!grid) return;

    var tag = grid.dataset.tag || '';
    var baseInterval = parseInt(grid.dataset.interval || '15', 10) * 1000;
    var speed = parseInt(grid.dataset.speed || '3', 10);
    var mosaicInterval = Math.max(2000, Math.round(baseInterval / Math.max(1, speed)));
    var fadeSec = parseFloat(grid.dataset.crossfade || '2');

    // Calculate how many tiles fill the viewport
    var tileMin = 280;
    _mosaicCols = Math.max(1, Math.floor(window.innerWidth / tileMin));
    var rowH = window.innerHeight / Math.max(1, Math.floor(window.innerHeight / (tileMin * 0.75)));
    var rows = Math.max(1, Math.round(window.innerHeight / rowH));
    var tileCount = _mosaicCols * rows;

    var url = API_PREFIX + '/images/random?count=' + tileCount;
    if (tag) url += '&tag=' + encodeURIComponent(tag);

    fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var images = data.images || [];
            if (images.length === 0) return;

            grid.innerHTML = '';
            // Fill the grid — repeat images if we have fewer than needed
            for (var i = 0; i < tileCount; i++) {
                var img = images[i % images.length];
                var tile = document.createElement('div');
                tile.className = 'mosaic-tile';
                var el = document.createElement('img');
                el.src = API_PREFIX + '/images/' + img.id + '/thumbnail';
                el.dataset.imageId = String(img.id);
                el.draggable = false;
                el.style.transitionDuration = fadeSec + 's';
                _applyKenBurns(el, grid);
                tile.appendChild(el);
                grid.appendChild(tile);
            }

            _mosaicTimer = setInterval(function() { _rotateMosaicTile(); }, mosaicInterval);
        })
        .catch(function() {});
}

function _rotateMosaicTile() {
    var grid = document.getElementById('mosaic-grid');
    if (!grid) return;
    var tiles = grid.querySelectorAll('.mosaic-tile');
    if (tiles.length === 0) return;

    // Pick a random tile, avoiding adjacent to the last one changed
    var idx, attempts = 0;
    do {
        idx = Math.floor(Math.random() * tiles.length);
        attempts++;
    } while (_isMosaicAdjacent(idx, _mosaicLastIdx) && attempts < 10);
    _mosaicLastIdx = idx;

    var tile = tiles[idx];
    var oldImg = tile.querySelector('img');
    var tag = grid.dataset.tag || '';
    var fadeSec = parseFloat(grid.dataset.crossfade || '2');

    // Collect all visible IDs to exclude
    var excludeIds = [];
    tiles.forEach(function(t) {
        var im = t.querySelector('img');
        if (im && im.dataset.imageId) excludeIds.push(im.dataset.imageId);
    });

    var url = API_PREFIX + '/images/random?count=1';
    if (tag) url += '&tag=' + encodeURIComponent(tag);
    if (excludeIds.length) url += '&exclude=' + excludeIds.join(',');

    fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var images = data.images || [];
            if (images.length === 0) return;
            var img = images[0];

            // Create new image on top, starting invisible
            var newImg = document.createElement('img');
            newImg.style.opacity = '0';
            newImg.style.transitionDuration = fadeSec + 's';
            newImg.dataset.imageId = String(img.id);
            newImg.draggable = false;
            _applyKenBurns(newImg, grid);
            tile.appendChild(newImg);

            // Preload then crossfade
            newImg.onload = function() {
                newImg.offsetHeight; // force reflow
                newImg.style.opacity = '1';
                if (oldImg) oldImg.style.opacity = '0';
                // Remove old image after transition
                setTimeout(function() {
                    if (oldImg && oldImg.parentNode) oldImg.parentNode.removeChild(oldImg);
                }, (fadeSec * 1000) + 100);
            };
            newImg.src = API_PREFIX + '/images/' + img.id + '/thumbnail';
        })
        .catch(function() {});
}

// Auto-init mosaic page
if (document.getElementById('mosaic-grid')) {
    initMosaic();
}

// ── Settings: Model Dropdowns ──────────────────────────────

function modelMatches(modelName, configValue) {
    // Handle cases like config="minicpm-v" matching API name="minicpm-v:latest"
    if (!configValue) return false;
    if (modelName === configValue) return true;
    if (modelName.replace(/:latest$/, '') === configValue) return true;
    if (modelName === configValue + ':latest') return true;
    return false;
}

function populateModelDropdowns(data) {
    const llmSelect = document.getElementById('llm_model');
    if (!llmSelect) return;

    const currentLlm = llmSelect.dataset.current || '';
    const visionModels = data.vision || [];
    const allModels = data.all || [];

    // Vision datalist: only vision-capable models, as suggestions for the text input
    const visionList = document.getElementById('vision_model_list');
    if (visionList) {
        visionList.innerHTML = '';
        visionModels.forEach(name => {
            const opt = document.createElement('option');
            opt.value = name;
            visionList.appendChild(opt);
        });
    }

    // LLM dropdown: all models (any model can do text generation)
    llmSelect.innerHTML = '';
    const noneOpt = document.createElement('option');
    noneOpt.value = '';
    noneOpt.textContent = 'None \u2014 keyword search only';
    if (!currentLlm) noneOpt.selected = true;
    llmSelect.appendChild(noneOpt);

    allModels.forEach(name => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        if (modelMatches(name, currentLlm)) opt.selected = true;
        llmSelect.appendChild(opt);
    });
}

function _fetchAndPopulateModels(resultDiv, visionSelect) {
    // Step 1: Quick connection check
    fetch('/api/health')
        .then(r => r.json())
        .then(data => {
            const connected = data.ollama && data.ollama.connected;
            if (!connected) {
                if (resultDiv) resultDiv.innerHTML = '<span class="badge badge-error">Failed</span> <small>Could not reach Ollama</small>';
                return;
            }
            if (resultDiv) resultDiv.innerHTML = '<span class="badge badge-processing">Loading models...</span>';

            // Step 2: Fetch categorised models
            fetch('/api/models')
                .then(r => r.json())
                .then(modelData => {
                    const total = (modelData.all || []).length;
                    const vCount = (modelData.vision || []).length;
                    if (resultDiv) resultDiv.innerHTML = '<span class="badge badge-success">Connected</span> <small>' + total + ' model(s) found (' + vCount + ' vision)</small>';
                    populateModelDropdowns(modelData);
                })
                .catch(() => {
                    if (resultDiv) resultDiv.innerHTML = '<span class="badge badge-warning">Connected</span> <small>Could not load model details</small>';
                });
        })
        .catch(() => {
            if (resultDiv) resultDiv.innerHTML = '<span class="badge badge-error">Failed</span> <small>Connection error</small>';
        });
}

function loadModels() {
    const resultDiv = document.getElementById('connection-result');
    const visionSelect = document.getElementById('vision_model');
    if (!resultDiv) return;
    resultDiv.innerHTML = '<span class="badge badge-processing">Connecting...</span>';
    _fetchAndPopulateModels(resultDiv, visionSelect);
}

// ── Page Init ─────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', function() {
    // Settings page: auto-load model dropdowns + init processing mode cards
    const visionSelect = document.getElementById('vision_model');
    if (visionSelect) {
        _fetchAndPopulateModels(null, visionSelect);
        toggleCatalogueMode();
        toggleRenameCards();
    }

    // Dashboard page: start live polling + init upload/workspace
    if (document.getElementById('stat-total')) {
        updateDashboard();
        setInterval(updateDashboard, 5000);

        // Library upload drop zone
        initUploadDropZone('upload-drop-zone', 'upload-file-input', function(files) {
            var input = document.getElementById('upload-subfolder');
            var subfolder = input ? input.value : '';
            handleLibraryUpload(files, subfolder);
        });

        // Workspace upload drop zones + stats polling
        initUploadDropZone('ws-drop-zone', 'ws-file-input', handleWorkspaceUpload);
        initUploadDropZone('ws-drop-zone-active', 'ws-file-input-active', handleWorkspaceUpload);
        if (document.getElementById('workspace-card')) {
            pollWorkspaceStats();
        }
    }

    // Dim delete buttons when destructive mode is off
    document.querySelectorAll('.btn-error').forEach(function(btn) {
        var text = btn.textContent.trim().toLowerCase();
        if (text === 'delete' || text.startsWith('delete ') || text === 'empty trash') {
            var isWs = typeof API_PREFIX !== 'undefined' && API_PREFIX.includes('workspace');
            var enabled = isWs ? window.DESTRUCTIVE_WORKSPACE : window.DESTRUCTIVE_LIBRARY;
            if (!enabled) btn.classList.add('destructive-disabled');
        }
    });
});

// ── Review Page: Selection Toolbar ──────────────────────────

function reviewToggleSelectAll() {
    var checkboxes = document.querySelectorAll('.review-checkbox');
    var btn = document.getElementById('review-select-toggle');
    var allChecked = Array.from(checkboxes).every(function(cb) { return cb.checked; });
    checkboxes.forEach(function(cb) { cb.checked = !allChecked; });
    if (btn) btn.textContent = allChecked ? 'Select All' : 'Select None';
    reviewUpdateToolbar();
}

function reviewUpdateToolbar() {
    var checkboxes = document.querySelectorAll('.review-checkbox');
    var checked = document.querySelectorAll('.review-checkbox:checked');
    var count = checked.length;
    var total = checkboxes.length;

    var approveBtn = document.getElementById('review-approve-selected');
    var skipBtn = document.getElementById('review-skip-selected');
    var reprocessBtn = document.getElementById('review-reprocess-selected');
    var countSpan = document.getElementById('review-selected-count');
    var toggleBtn = document.getElementById('review-select-toggle');

    if (approveBtn) approveBtn.disabled = count === 0;
    if (skipBtn) skipBtn.disabled = count === 0;
    if (reprocessBtn) reprocessBtn.disabled = count === 0;
    if (countSpan) countSpan.textContent = count > 0 ? count + ' selected' : '';
    if (toggleBtn) toggleBtn.textContent = (count === total && total > 0) ? 'Select None' : 'Select All';

    // Toggle selected highlight on cards
    checkboxes.forEach(function(cb) {
        var card = cb.closest('.review-card');
        if (card) card.classList.toggle('selected', cb.checked);
    });
}

function reviewApproveSelected() {
    var ids = [];
    document.querySelectorAll('.review-checkbox:checked').forEach(function(cb) {
        ids.push(parseInt(cb.value));
    });
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }

    fetch(API_PREFIX + '/images/approve-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_ids: ids })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        showToast('Approved ' + (data.results || []).length + ' images');
        setTimeout(function() { window.location.reload(); }, 500);
    })
    .catch(function() { showToast('Request failed', 'error'); });
}

function reviewSkipSelected() {
    var ids = [];
    document.querySelectorAll('.review-checkbox:checked').forEach(function(cb) {
        ids.push(parseInt(cb.value));
    });
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }

    Promise.all(ids.map(function(id) {
        return fetch(API_PREFIX + '/images/' + id + '/skip', { method: 'POST' });
    }))
    .then(function() {
        showToast('Skipped ' + ids.length + ' images');
        setTimeout(function() { window.location.reload(); }, 500);
    })
    .catch(function() { showToast('Request failed', 'error'); });
}

function reviewReprocessSelected() {
    var ids = [];
    document.querySelectorAll('.review-checkbox:checked').forEach(function(cb) {
        ids.push(parseInt(cb.value));
    });
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }

    showContextModal('Reprocess ' + ids.length + ' Images', '', function(context) {
        var body = { image_ids: ids };
        if (context !== null) body.context = context;
        fetch(API_PREFIX + '/images/process-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        })
        .then(function(r) { return r.json(); })
        .then(function() {
            showToast('Reprocessing ' + ids.length + ' images');
            setTimeout(function() { window.location.reload(); }, 500);
        })
        .catch(function() { showToast('Request failed', 'error'); });
    });
}

// ── Review Page Actions ─────────────────────────────────────

function approveImage(imageId) {
    const input = document.getElementById('proposed-' + imageId);
    const filename = input ? input.value.trim() : '';

    fetch(API_PREFIX + '/images/' + imageId + '/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: filename || null })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'renamed') {
            const card = document.getElementById('review-card-' + imageId);
            if (card) card.remove();
            showToast('Approved: ' + (data.new_name || filename));
        } else {
            showToast('Error: ' + (data.detail || 'Unknown'), 'error');
        }
    })
    .catch(() => showToast('Request failed', 'error'));
}

// ── Review Page: Skip / Reprocess ────────────────────────────

function reviewSkip(imageId) {
    fetch(API_PREFIX + '/images/' + imageId + '/skip', { method: 'POST' })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
        if (ok) {
            var card = document.getElementById('review-card-' + imageId);
            if (card) card.remove();
            showToast('Skipped');
        } else {
            showToast(data.detail || 'Skip failed', 'error');
        }
    })
    .catch(() => showToast('Request failed', 'error'));
}

function reviewReprocess(imageId) {
    var card = document.getElementById('review-card-' + imageId);
    var prevContext = card ? card.dataset.context || '' : '';
    showContextModal('Reprocess with Context', prevContext, function(context) {
        var opts = { method: 'POST' };
        if (context !== null) {
            opts.headers = { 'Content-Type': 'application/json' };
            opts.body = JSON.stringify({ context: context });
        }
        fetch(API_PREFIX + '/images/' + imageId + '/process', opts)
        .then(r => r.json().then(data => ({ ok: r.ok, data })))
        .then(({ ok, data }) => {
            if (ok) {
                if (card) card.remove();
                showToast('Reprocessing...');
            } else {
                showToast(data.detail || 'Reprocess failed', 'error');
            }
        })
        .catch(() => showToast('Request failed', 'error'));
    });
}

// ── Dashboard: Approve All Proposed ─────────────────────────

function approveAllProposed(total) {
    showConfirm('Approve all ' + total + ' proposed images?',
        'This will approve and rename all proposed images, including any not visible on this page.',
        'Approve All',
        function() {
            fetch(API_PREFIX + '/images?status=proposed&limit=1')
            .then(function(r) { return r.json(); })
            .then(function(countData) {
                var serverTotal = countData.total || 0;
                if (serverTotal === 0) {
                    showToast('No proposed images to approve');
                    return;
                }
                var allIds = [];
                var batchSize = 200;
                function fetchBatch(offset) {
                    return fetch(API_PREFIX + '/images?status=proposed&limit=' + batchSize + '&offset=' + offset)
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        var ids = (data.images || []).map(function(img) { return img.id; });
                        allIds = allIds.concat(ids);
                        if (ids.length >= batchSize) return fetchBatch(offset + batchSize);
                    });
                }
                return fetchBatch(0).then(function() {
                    showToast('Approving ' + allIds.length + ' images...');
                    return fetch(API_PREFIX + '/images/approve-batch', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ image_ids: allIds })
                    });
                })
                .then(function(r) { return r.json(); })
                .then(function(result) {
                    showToast('Approved ' + (result.results || []).length + ' images');
                    setTimeout(function() { window.location.reload(); }, 500);
                });
            })
            .catch(function() { showToast('Request failed', 'error'); });
        },
        'btn-success');
}

// ── Queue: Single-Row Actions ──────────────────────────────

function _updateQueueRow(imageId, newStatus, actionHtml) {
    var statusCell = document.getElementById('queue-status-' + imageId);
    if (statusCell) {
        statusCell.innerHTML = '<span class="badge badge-' + newStatus + '">' + newStatus + '</span>';
    }
    var actionsCell = document.getElementById('queue-actions-' + imageId);
    if (actionsCell) {
        actionsCell.innerHTML = actionHtml;
    }
}

function _updateQueueNames(imageId, currentName, label, otherName) {
    var currentCell = document.getElementById('queue-current-' + imageId);
    if (currentCell) {
        currentCell.textContent = currentName;
        currentCell.title = currentName;
    }
    var labelCell = document.getElementById('queue-label-' + imageId);
    if (labelCell) {
        if (label === 'proposed') {
            labelCell.innerHTML = '<span class="name-label-proposed">proposed</span>';
        } else if (label === 'was') {
            labelCell.innerHTML = '<span class="name-label-was">was</span>';
        } else {
            labelCell.innerHTML = '';
        }
    }
    var otherCell = document.getElementById('queue-other-' + imageId);
    if (otherCell) {
        if (label === 'proposed') {
            otherCell.innerHTML = '<span class="name-other-proposed" title="' + _esc(otherName) + '">' + _esc(otherName) + '</span>';
        } else if (label === 'was') {
            otherCell.innerHTML = '<span class="name-other-was" title="' + _esc(otherName) + '">' + _esc(otherName) + '</span>';
        } else {
            otherCell.textContent = otherName || '-';
            otherCell.title = '';
        }
    }
}

function _getStatusFilter() {
    return new URLSearchParams(window.location.search).get('status') || '';
}

function _removeRow(imageId) {
    var row = document.getElementById('image-row-' + imageId);
    if (row) {
        row.style.transition = 'opacity 0.3s ease';
        row.style.opacity = '0';
        setTimeout(function() { row.remove(); }, 300);
    }
}

function _removeRows(ids) {
    ids.forEach(function(id) { _removeRow(id); });
}

function _decrementTabCount(n) {
    var activeTab = document.querySelector('.status-tab.active .tab-count');
    if (activeTab) {
        var count = Math.max(0, parseInt(activeTab.textContent || '0') - n);
        activeTab.textContent = count;
    }
}

function queueApprove(imageId) {
    var prevName = (document.getElementById('queue-current-' + imageId) || {}).textContent || '';
    fetch(API_PREFIX + '/images/' + imageId + '/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: null })
    })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
        if (ok && data.status === 'renamed') {
            if (_getStatusFilter()) {
                _removeRow(imageId);
                _decrementTabCount(1);
            } else {
                _updateQueueRow(imageId, 'renamed',
                    '<button class="btn btn-xs btn-warning" onclick="queueRevert(' + imageId + ')">Revert</button>');
                _updateQueueNames(imageId, data.new_name || '', 'was', prevName);
            }
            showToast('Approved: ' + (data.new_name || ''));
        } else {
            showToast(data.detail || 'Approve failed', 'error');
        }
    })
    .catch(() => showToast('Request failed', 'error'));
}

function queueProcess(imageId) {
    // Get existing context from the row's data attribute
    var row = document.querySelector('tr[data-id="' + imageId + '"]');
    var prevContext = row ? row.dataset.context || '' : '';
    showContextModal('Reprocess with Context', prevContext, function(context) {
        var opts = { method: 'POST' };
        if (context !== null) {
            opts.headers = { 'Content-Type': 'application/json' };
            opts.body = JSON.stringify({ context: context });
        }
        fetch(API_PREFIX + '/images/' + imageId + '/process', opts)
        .then(r => r.json().then(data => ({ ok: r.ok, data })))
        .then(({ ok, data }) => {
            if (ok) {
                if (_getStatusFilter()) {
                    _removeRow(imageId);
                    _decrementTabCount(1);
                } else {
                    _updateQueueRow(imageId, 'processing', '<span class="text-muted">Processing...</span>');
                }
                showToast('Queued for processing');
            } else {
                showToast(data.detail || 'Process failed', 'error');
            }
        })
        .catch(() => showToast('Request failed', 'error'));
    });
}

function queueRevert(imageId) {
    var prevName = (document.getElementById('queue-current-' + imageId) || {}).textContent || '';
    fetch(API_PREFIX + '/images/' + imageId + '/revert', { method: 'POST' })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
        if (ok && data.status === 'reverted') {
            if (_getStatusFilter()) {
                _removeRow(imageId);
                _decrementTabCount(1);
            } else {
                _updateQueueRow(imageId, 'proposed',
                    '<button class="btn btn-xs btn-success" onclick="queueApprove(' + imageId + ')">Approve</button>');
                _updateQueueNames(imageId, data.restored_name || '', 'proposed', prevName);
            }
            showToast('Reverted: ' + (data.restored_name || ''));
        } else {
            showToast(data.detail || 'Revert failed', 'error');
        }
    })
    .catch(() => showToast('Request failed', 'error'));
}

function queueSkip(imageId) {
    fetch(API_PREFIX + '/images/' + imageId + '/skip', { method: 'POST' })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
        if (ok) {
            if (_getStatusFilter()) {
                _removeRow(imageId);
                _decrementTabCount(1);
            } else {
                _updateQueueRow(imageId, 'skipped',
                    '<button class="btn btn-xs btn-primary" onclick="queueUnskip(' + imageId + ')">Un-skip</button>');
            }
            showToast('Skipped');
        } else {
            showToast(data.detail || 'Skip failed', 'error');
        }
    })
    .catch(() => showToast('Request failed', 'error'));
}

function queueUnskip(imageId) {
    fetch(API_PREFIX + '/images/' + imageId + '/unskip', { method: 'POST' })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
        if (ok) {
            if (_getStatusFilter()) {
                _removeRow(imageId);
                _decrementTabCount(1);
            } else if (data.reprocessed) {
                _updateQueueRow(imageId, 'processing', '<span class="text-muted">Processing...</span>');
                _updateQueueNames(imageId, (document.getElementById('queue-current-' + imageId) || {}).textContent || '', '', '-');
            } else {
                _updateQueueRow(imageId, 'proposed',
                    '<button class="btn btn-xs btn-success" onclick="queueApprove(' + imageId + ')">Approve</button>');
            }
            showToast(data.reprocessed ? 'Un-skipped — queued for processing' : 'Un-skipped — returned to proposed');
        } else {
            showToast(data.detail || 'Un-skip failed', 'error');
        }
    })
    .catch(() => showToast('Request failed', 'error'));
}

// ── Queue: Bulk Actions ─────────────────────────────────────

function toggleSelectAll(checkbox) {
    document.querySelectorAll('.image-checkbox').forEach(cb => {
        cb.checked = checkbox.checked;
    });
}

function getSelectedIds() {
    const ids = [];
    document.querySelectorAll('.image-checkbox:checked').forEach(cb => {
        ids.push(parseInt(cb.value));
    });
    return ids;
}

function bulkAction(action) {
    const ids = getSelectedIds();
    if (ids.length === 0) {
        showToast('No images selected', 'error');
        return;
    }

    if (action === 'process') {
        showContextModal('Reprocess ' + ids.length + ' Images', '', function(context) {
            var body = { image_ids: ids };
            if (context !== null) body.context = context;
            _doBulkAction(action, ids, API_PREFIX + '/images/process-batch', body);
        });
        return;
    }

    let url, body;
    if (action === 'approve') {
        url = API_PREFIX + '/images/approve-batch';
        body = { image_ids: ids };
    }

    _doBulkAction(action, ids, url, body);
}

function _doBulkAction(action, ids, url, body) {
    fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    })
    .then(r => r.json())
    .then(data => {
        showToast((action === 'approve' ? 'Approved' : 'Reprocessing') + ': ' + ids.length + ' images');
        if (_getStatusFilter()) {
            _removeRows(ids);
            _decrementTabCount(ids.length);
        } else {
            setTimeout(() => window.location.reload(), 500);
        }
    })
    .catch(() => showToast('Request failed', 'error'));
}

function bulkSkip() {
    const ids = getSelectedIds();
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }

    Promise.all(ids.map(id =>
        fetch(API_PREFIX + '/images/' + id + '/skip', { method: 'POST' })
    )).then(() => {
        showToast('Skipped ' + ids.length + ' images');
        if (_getStatusFilter()) {
            _removeRows(ids);
            _decrementTabCount(ids.length);
        } else {
            setTimeout(() => window.location.reload(), 500);
        }
    }).catch(() => showToast('Request failed', 'error'));
}

function bulkUnskip() {
    const ids = getSelectedIds();
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }

    Promise.all(ids.map(id =>
        fetch(API_PREFIX + '/images/' + id + '/unskip', { method: 'POST' })
    )).then(() => {
        showToast('Un-skipped ' + ids.length + ' images');
        if (_getStatusFilter()) {
            _removeRows(ids);
            _decrementTabCount(ids.length);
        } else {
            setTimeout(() => window.location.reload(), 500);
        }
    }).catch(() => showToast('Request failed', 'error'));
}

function bulkRevert() {
    const ids = getSelectedIds();
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }

    showConfirm('Revert ' + ids.length + ' files?',
        'These files will be renamed back to their original filenames.',
        'Revert',
        function() {
            Promise.all(ids.map(id =>
                fetch(API_PREFIX + '/images/' + id + '/revert', { method: 'POST' })
            )).then(() => {
                showToast('Reverted ' + ids.length + ' images');
                if (_getStatusFilter()) {
                    _removeRows(ids);
                    _decrementTabCount(ids.length);
                } else {
                    setTimeout(() => window.location.reload(), 500);
                }
            }).catch(() => showToast('Some reverts failed', 'error'));
        });
}

// ── Destructive Mode Helpers ─────────────────────────────────

function showDestructiveModal(area) {
    var msg = 'File deletion is disabled for the ' + area + ' to prevent accidental data loss. You can enable it in Settings.';
    document.getElementById('destructive-modal-message').textContent = msg;
    document.getElementById('destructive-modal').style.display = '';
}

function closeDestructiveModal() {
    document.getElementById('destructive-modal').style.display = 'none';
}

// ── Context Modal ────────────────────────────────────────────

var _contextCallback = null;

function showContextModal(title, previousContext, callback) {
    document.getElementById('context-modal-title').textContent = title || 'Reprocess with Context';
    var prevEl = document.getElementById('context-modal-previous');
    var prevText = document.getElementById('context-modal-prev-text');
    var input = document.getElementById('context-modal-input');
    if (previousContext) {
        prevEl.style.display = '';
        prevText.textContent = previousContext;
        input.value = previousContext;
    } else {
        prevEl.style.display = 'none';
        input.value = '';
    }
    _contextCallback = callback;
    document.getElementById('context-modal').style.display = '';
    input.focus();
}

function contextModalOk() {
    document.getElementById('context-modal').style.display = 'none';
    var val = document.getElementById('context-modal-input').value.trim();
    if (_contextCallback) _contextCallback(val || null);
    _contextCallback = null;
}

function contextModalSkip() {
    document.getElementById('context-modal').style.display = 'none';
    if (_contextCallback) _contextCallback(null);
    _contextCallback = null;
}

function contextModalCancel() {
    document.getElementById('context-modal').style.display = 'none';
    _contextCallback = null;
}

// ── Trash Actions ────────────────────────────────────────────

function trashOne(imageId) {
    if (!window.DESTRUCTIVE_LIBRARY) { showDestructiveModal('library'); return; }
    showConfirm('Delete file?',
        'This file will be moved to the trash. You can restore it or permanently delete it from the Trashed tab.',
        'Delete',
        function() {
            fetch(API_PREFIX + '/images/' + imageId + '/trash', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                _removeRow(imageId);
                _decrementTabCount(1);
                showToast('Moved to trash');
            })
            .catch(() => showToast('Failed to delete image', 'error'));
        });
}

function restoreOne(imageId) {
    fetch(API_PREFIX + '/images/' + imageId + '/restore', { method: 'POST' })
    .then(r => r.json())
    .then(data => {
        _removeRow(imageId);
        _decrementTabCount(1);
        showToast('Restored from trash');
    })
    .catch(() => showToast('Failed to restore image', 'error'));
}

function bulkTrash() {
    if (!window.DESTRUCTIVE_LIBRARY) { showDestructiveModal('library'); return; }
    const ids = getSelectedIds();
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }

    showConfirm('Delete ' + ids.length + ' files?',
        'These files will be moved to the trash. You can restore them or permanently delete them from the Trashed tab.',
        'Delete ' + ids.length + ' files',
        function() {
            fetch(API_PREFIX + '/images/trash-batch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: ids })
            })
            .then(r => r.json())
            .then(data => {
                showToast('Deleted ' + (data.trashed || 0) + ' images');
                _removeRows(ids);
                _decrementTabCount(ids.length);
            })
            .catch(() => showToast('Failed to delete images', 'error'));
        });
}

// ── Workspace Delete ─────────────────────────────────────────
function wsDeleteOne(imageId) {
    if (!window.DESTRUCTIVE_WORKSPACE) { showDestructiveModal('workspace'); return; }
    showConfirm('Permanently delete?',
        'This file will be permanently deleted. This cannot be undone.',
        'Delete',
        function() {
            fetch(API_PREFIX + '/images/' + imageId, { method: 'DELETE' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                // Remove from queue table
                _removeRow(imageId);
                // Remove from review grid
                var card = document.getElementById('review-card-' + imageId);
                if (card) {
                    card.style.transition = 'opacity 0.3s ease';
                    card.style.opacity = '0';
                    setTimeout(function() { card.remove(); }, 300);
                }
                _decrementTabCount(1);
                showToast('Deleted permanently');
            })
            .catch(function() { showToast('Failed to delete', 'error'); });
        });
}

function wsBulkDelete() {
    if (!window.DESTRUCTIVE_WORKSPACE) { showDestructiveModal('workspace'); return; }
    var ids = getSelectedIds();
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }

    showConfirm('Permanently delete ' + ids.length + ' files?',
        'These files will be permanently deleted. This cannot be undone.',
        'Delete ' + ids.length + ' files',
        function() {
            fetch(API_PREFIX + '/images/delete-batch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ image_ids: ids })
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                showToast('Deleted ' + (data.deleted || 0) + ' images');
                _removeRows(ids);
                _decrementTabCount(ids.length);
            })
            .catch(function() { showToast('Failed to delete images', 'error'); });
        });
}

function bulkRestore() {
    const ids = getSelectedIds();
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }

    Promise.all(ids.map(id =>
        fetch(API_PREFIX + '/images/' + id + '/restore', { method: 'POST' })
    )).then(() => {
        showToast('Restored ' + ids.length + ' images');
        _removeRows(ids);
        _decrementTabCount(ids.length);
    }).catch(() => showToast('Some restores failed', 'error'));
}

function emptyTrash() {
    if (!window.DESTRUCTIVE_LIBRARY) { showDestructiveModal('library'); return; }
    fetch('/api/trash/stats')
    .then(r => r.json())
    .then(stats => {
        if (stats.count === 0) {
            showToast('Trash is already empty');
            return;
        }
        showConfirm('Permanently delete ' + stats.count + ' files?',
            'This will permanently delete ' + stats.count + ' files (' + stats.total_size_human + '). This cannot be undone.',
            'Permanently Delete',
            function() {
                fetch('/api/trash', { method: 'DELETE' })
                .then(r => r.json())
                .then(data => {
                    showToast('Permanently deleted ' + (data.deleted || 0) + ' files');
                    setTimeout(() => window.location.reload(), 500);
                })
                .catch(() => showToast('Failed to empty trash', 'error'));
            });
    })
    .catch(() => showToast('Failed to get trash stats', 'error'));
}

// ── Search Page ──────────────────────────────────────────────

var _searchResults = [];

function executeSearch() {
    var input = document.getElementById('search-input');
    var query = input ? input.value.trim() : '';
    if (!query) { showToast('Enter a search query', 'error'); return; }

    var loading = document.getElementById('search-loading');
    var info = document.getElementById('search-info');
    var results = document.getElementById('search-results');
    var empty = document.getElementById('search-empty');
    var btn = document.getElementById('search-btn');

    var toolbar = document.getElementById('search-toolbar');

    if (loading) {
        loading.style.display = '';
        var tagline = document.getElementById('search-tagline');
        if (tagline) tagline.textContent = _randomQuote();
    }
    if (info) info.style.display = 'none';
    if (results) results.innerHTML = '';
    if (empty) empty.style.display = 'none';
    if (toolbar) toolbar.style.display = 'none';
    if (btn) btn.disabled = true;

    var requestBody = { query: query };
    var keywordToggle = document.getElementById('keyword-only-toggle');
    if (keywordToggle && keywordToggle.checked) {
        requestBody.use_llm = false;
    }

    var searchStartTime = Date.now();
    var SEARCH_MIN_DISPLAY = 3000;

    fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        var elapsed = Date.now() - searchStartTime;
        var remaining = Math.max(0, SEARCH_MIN_DISPLAY - elapsed);
        setTimeout(function() { _showSearchResults(data, loading, info, results, empty, toolbar, btn); }, remaining);
        return;
    })
    .catch(function() {
        if (loading) loading.style.display = 'none';
        if (btn) btn.disabled = false;
        showToast('Search failed', 'error');
    });
}

function _showSearchResults(data, loading, info, results, empty, toolbar, btn) {
        if (loading) loading.style.display = 'none';
        if (btn) btn.disabled = false;

        _searchResults = data.results || [];
        var mode = data.mode || 'structured';
        var interp = data.query_interpretation || {};

        // Show info bar
        if (info) info.style.display = '';
        var countEl = document.getElementById('search-result-count');
        if (countEl) countEl.textContent = 'Found ' + _searchResults.length + ' results';

        var modeEl = document.getElementById('search-mode-badge');
        if (modeEl) {
            modeEl.textContent = mode === 'llm' ? 'LLM Search' : (mode === 'llm_fallback' ? 'LLM Fallback' : 'Keyword Search');
            modeEl.className = 'badge ' + (mode === 'llm' ? 'badge-success' : 'badge-proposed');
        }

        // Query interpretation
        var interpEl = document.getElementById('search-interpretation');
        var interpJson = document.getElementById('search-interpretation-json');
        if (interpEl && interpJson && Object.keys(interp).length > 0) {
            interpEl.style.display = '';
            interpJson.textContent = JSON.stringify(interp, null, 2);
        }

        // Reset slider to 25% default
        var slider = document.getElementById('relevance-slider');
        if (slider) slider.value = 25;
        var sliderVal = document.getElementById('relevance-value');
        if (sliderVal) sliderVal.textContent = '25%';

        // Render results
        if (_searchResults.length === 0) {
            if (empty) empty.style.display = '';
        } else {
            renderSearchResults(_searchResults);
            if (toolbar) toolbar.style.display = '';
            filterByRelevance(25);
        }
}

function renderSearchResults(items) {
    var container = document.getElementById('search-results');
    if (!container) return;
    container.innerHTML = '';

    items.forEach(function(img) {
        var score = img.relevance_score || 0;
        var scorePct = Math.round(score * 100);
        var scoreClass = scorePct >= 70 ? 'high' : (scorePct >= 40 ? 'medium' : 'low');

        var desc = img.vision_description || '';
        if (desc.length > 120) desc = desc.substring(0, 120) + '...';

        var qualityBadges = '';
        if (img.quality_flags && img.quality_flags.length > 0) {
            img.quality_flags.forEach(function(flag) {
                qualityBadges += '<span class="badge badge-quality">' + _esc(flag) + '</span> ';
            });
        }

        // Build match explanation tooltip from match_details
        var matchTitle = '';
        if (img.match_details && img.match_details.length > 0) {
            matchTitle = img.match_details.map(function(m) {
                return '\u0022' + _esc(m.keyword) + '\u0022 in ' + _esc(m.found_in.join(', ')) + (m.partial ? ' (partial)' : '');
            }).join('\n');
        }
        var matchTitleAttr = matchTitle ? ' title="' + matchTitle.replace(/"/g, '&quot;') + '"' : '';

        var card = document.createElement('div');
        card.className = 'review-card search-result-card';
        card.dataset.relevance = scorePct;
        card.dataset.imageId = img.id;

        card.innerHTML =
            '<div class="review-image">' +
                '<input type="checkbox" class="image-checkbox search-checkbox" value="' + img.id + '" onchange="searchUpdateToolbar()">' +
                '<img src="/api/images/' + img.id + '/thumbnail" alt="" loading="lazy" ' +
                    'onclick="openImageModal(' + img.id + ')" style="cursor:pointer" ' +
                    'onerror="this.src=\'/static/css/placeholder.svg\'">' +
            '</div>' +
            '<div class="review-body">' +
                '<div class="review-original">' +
                    '<span class="filename">' + _esc(img.current_filename || img.original_filename || '') + '</span>' +
                '</div>' +
                (desc ? '<p class="text-muted" style="font-size:0.85rem;margin:0.3rem 0">' + _esc(desc) + '</p>' : '') +
                '<div class="review-meta">' +
                    '<span class="search-relevance search-relevance-' + scoreClass + '"' + matchTitleAttr + ' style="cursor:help">' + scorePct + '% match</span> ' +
                    (img.exif_date ? '<span class="meta-badge">' + _esc(img.exif_date) + '</span> ' : '') +
                    (img.location_name ? '<span class="meta-badge">' + _esc(img.location_name) + '</span> ' : '') +
                    (img.camera_model ? '<span class="meta-badge">' + _esc(img.camera_model) + '</span> ' : '') +
                    '<span class="badge badge-' + _esc(img.status) + '">' + _esc(img.status) + '</span> ' +
                    qualityBadges +
                '</div>' +
            '</div>';

        container.appendChild(card);
    });
}

function filterByRelevance(threshold) {
    var valEl = document.getElementById('relevance-value');
    if (valEl) valEl.textContent = threshold + '%';

    var cards = document.querySelectorAll('.search-result-card');
    var shown = 0;
    cards.forEach(function(card) {
        var rel = parseInt(card.dataset.relevance || '0', 10);
        if (rel >= parseInt(threshold, 10)) {
            card.style.display = '';
            shown++;
        } else {
            card.style.display = 'none';
        }
    });

    var countEl = document.getElementById('search-result-count');
    if (countEl) countEl.textContent = 'Showing ' + shown + ' of ' + _searchResults.length + ' results';
}

// ── Search Batch Actions ─────────────────────────────────────

function searchToggleSelectAll() {
    var checkboxes = document.querySelectorAll('.search-checkbox');
    var btn = document.getElementById('search-select-toggle');
    var allChecked = Array.from(checkboxes).every(function(cb) { return cb.checked; });
    checkboxes.forEach(function(cb) { cb.checked = !allChecked; });
    if (btn) btn.textContent = allChecked ? 'Select All' : 'Select None';
    searchUpdateToolbar();
}

function searchUpdateToolbar() {
    var checkboxes = document.querySelectorAll('.search-checkbox');
    var checked = document.querySelectorAll('.search-checkbox:checked');
    var count = checked.length;
    var total = checkboxes.length;

    var reprocessBtn = document.getElementById('search-reprocess-selected');
    var queueBtn = document.getElementById('search-queue-selected');
    var downloadBtn = document.getElementById('search-download-selected');
    var deleteBtn = document.getElementById('search-delete-selected');
    var countSpan = document.getElementById('search-selected-count');
    var toggleBtn = document.getElementById('search-select-toggle');

    if (reprocessBtn) reprocessBtn.disabled = count === 0;
    if (queueBtn) queueBtn.disabled = count === 0;
    if (downloadBtn) downloadBtn.disabled = count === 0;
    if (deleteBtn) deleteBtn.disabled = count === 0;
    if (countSpan) countSpan.textContent = count > 0 ? count + ' selected' : '';
    if (toggleBtn) toggleBtn.textContent = (count === total && total > 0) ? 'Select None' : 'Select All';

    checkboxes.forEach(function(cb) {
        var card = cb.closest('.review-card');
        if (card) card.classList.toggle('selected', cb.checked);
    });
}

function _getSearchSelectedIds() {
    var ids = [];
    document.querySelectorAll('.search-checkbox:checked').forEach(function(cb) {
        ids.push(parseInt(cb.value));
    });
    return ids;
}

function searchReprocessSelected() {
    var ids = _getSearchSelectedIds();
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }

    showContextModal('Reprocess ' + ids.length + ' Images', '', function(context) {
        var body = { image_ids: ids };
        if (context !== null) body.context = context;
        fetch(API_PREFIX + '/images/process-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        })
        .then(function(r) { return r.json(); })
        .then(function() {
            showToast('Reprocessing ' + ids.length + ' images');
        })
        .catch(function() { showToast('Request failed', 'error'); });
    });
}

function searchQueueAdd() {
    var ids = _getSearchSelectedIds();
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }
    fetch(API_PREFIX + '/images/queue-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: ids, action: 'add' })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        showToast('Added ' + (data.updated || 0) + ' images to queue');
    })
    .catch(function() { showToast('Failed to add to queue', 'error'); });
}

function searchDownloadSelected() {
    var ids = _getSearchSelectedIds();
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }

    showToast('Preparing download...');
    var xhr = new XMLHttpRequest();
    xhr.open('POST', API_PREFIX + '/images/download-batch', true);
    xhr.responseType = 'blob';
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.onload = function() {
        if (xhr.status === 200) {
            var blob = xhr.response;
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = 'a-eye_download.zip';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            showToast('Download started');
        } else {
            showToast('Download failed', 'error');
        }
    };
    xhr.onerror = function() { showToast('Download failed', 'error'); };
    xhr.send(JSON.stringify({ image_ids: ids }));
}

function searchDeleteSelected() {
    if (!window.DESTRUCTIVE_LIBRARY) { showDestructiveModal('library'); return; }
    var ids = _getSearchSelectedIds();
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }

    showConfirm('Delete ' + ids.length + ' files?',
        'These files will be moved to the trash. You can restore them or permanently delete them from the Trashed tab.',
        'Delete ' + ids.length + ' files',
        function() {
            fetch(API_PREFIX + '/images/trash-batch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: ids })
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                showToast('Deleted ' + (data.trashed || 0) + ' images');
                ids.forEach(function(id) {
                    var card = document.querySelector('.search-result-card[data-image-id="' + id + '"]');
                    if (card) card.remove();
                });
                searchUpdateToolbar();
            })
            .catch(function() { showToast('Failed to delete images', 'error'); });
        });
}

function saveLLMModel() {
    var select = document.getElementById('llm-model-select');
    if (!select || !select.value) { showToast('Select a model first', 'error'); return; }

    fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: { llm_model: select.value } })
    })
    .then(function(r) { return r.json(); })
    .then(function() {
        showToast('Text LLM enabled: ' + select.value);
        var banner = document.getElementById('llm-banner');
        if (banner) banner.style.display = 'none';
        // Update placeholder text
        var input = document.getElementById('search-input');
        if (input) input.placeholder = "Search with natural language, e.g. 'nighttime photos from London'";
    })
    .catch(function() { showToast('Failed to save model', 'error'); });
}

// Populate model dropdown on search page load
(function() {
    var select = document.getElementById('llm-model-select');
    if (!select) return;
    fetch('/api/models')
    .then(function(r) { return r.json(); })
    .then(function(data) {
        var models = (data.text || []).concat(data.all || []);
        // Deduplicate
        var seen = {};
        models.forEach(function(m) {
            if (!seen[m]) {
                seen[m] = true;
                var opt = document.createElement('option');
                opt.value = m;
                opt.textContent = m;
                select.appendChild(opt);
            }
        });
    })
    .catch(function() {});
})();

// ── History Page: Revert / Re-rename ─────────────────────────

function revertImage(historyId, imageId) {
    fetch(API_PREFIX + '/images/' + imageId + '/revert', { method: 'POST' })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
        if (ok && data.status === 'reverted') {
            showToast('Reverted: ' + (data.restored_name || ''));
            setTimeout(() => window.location.reload(), 500);
        } else {
            showToast(data.detail || 'Revert failed', 'error');
        }
    })
    .catch(() => showToast('Request failed', 'error'));
}

function clearRevertedHistory() {
    fetch('/api/history/reverted', { method: 'DELETE' })
    .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(data => {
        showToast('Cleared ' + (data.deleted || 0) + ' reverted entries');
        setTimeout(() => window.location.reload(), 500);
    })
    .catch(() => showToast('Request failed', 'error'));
}

function clearAllHistory() {
    showConfirm('Clear all history?',
        'This will delete all rename history. You will no longer be able to revert any active renames.',
        'Clear History',
        function() {
            fetch('/api/history/all', { method: 'DELETE' })
            .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
            .then(data => {
                showToast('Cleared ' + (data.deleted || 0) + ' history entries');
                setTimeout(() => window.location.reload(), 500);
            })
            .catch(() => showToast('Request failed', 'error'));
        });
}

// ── Image Detail Modal ──────────────────────────────────────

var _modalImageIds = [];
var _modalIndex = -1;

function _collectPageImageIds() {
    var ids = [];
    var seen = {};
    // Queue page: rows with id="image-row-{id}"
    var queueRows = document.querySelectorAll('[id^="image-row-"]');
    if (queueRows.length > 0) {
        queueRows.forEach(function(el) {
            var id = parseInt(el.id.replace('image-row-', ''));
            if (!isNaN(id) && !seen[id]) { ids.push(id); seen[id] = true; }
        });
        return ids;
    }
    // History page: rows with data-image-id
    document.querySelectorAll('[data-image-id]').forEach(function(el) {
        var id = parseInt(el.dataset.imageId);
        if (!isNaN(id) && !seen[id]) { ids.push(id); seen[id] = true; }
    });
    return ids;
}

function _updateModalNav() {
    var prev = document.getElementById('modal-prev');
    var next = document.getElementById('modal-next');
    var show = _modalImageIds.length > 1;
    if (prev) {
        prev.style.display = show ? '' : 'none';
        prev.disabled = _modalIndex <= 0;
    }
    if (next) {
        next.style.display = show ? '' : 'none';
        next.disabled = _modalIndex >= _modalImageIds.length - 1;
    }
}

var _exifSkipKeys = [
    'JPEGThumbnail', 'TIFFThumbnail', 'Thumbnail',
    'MakerNote', 'UserComment', 'ComponentsConfiguration',
    'FileSource', 'SceneType', 'PrintIM'
];

function _cleanExif(raw) {
    var cleaned = {};
    Object.keys(raw).forEach(function(key) {
        // Skip binary/noisy fields
        var dominated = _exifSkipKeys.some(function(skip) { return key.indexOf(skip) !== -1; });
        if (dominated) return;
        var val = raw[key];
        // Skip very long values (likely binary data encoded as string)
        if (typeof val === 'string' && val.length > 200) return;
        // Skip arrays longer than 20 items (pixel data, etc.)
        if (Array.isArray(val) && val.length > 20) return;
        // Clean up the key name: "EXIF DateTimeOriginal" -> "Date Time Original"
        var cleanKey = key
            .replace(/^(EXIF|Image|GPS|Interop|Thumbnail)\s+/, '')
            .replace(/([a-z])([A-Z])/g, '$1 $2');
        cleaned[cleanKey] = val;
    });
    return cleaned;
}

function _formatExif(cleaned) {
    var lines = [];
    var keys = Object.keys(cleaned).sort();
    var maxLen = 0;
    keys.forEach(function(k) { if (k.length > maxLen) maxLen = k.length; });
    keys.forEach(function(key) {
        var val = cleaned[key];
        if (Array.isArray(val)) val = val.join(', ');
        var pad = ' '.repeat(Math.max(0, maxLen - key.length + 2));
        lines.push(key + pad + val);
    });
    return lines.join('\n');
}

function _formatFileSize(bytes) {
    if (!bytes) return '-';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
}

function openImageModal(imageId, skipCollect) {
    if (!skipCollect) {
        _modalImageIds = _collectPageImageIds();
        _modalIndex = _modalImageIds.indexOf(imageId);
    }
    fetch(API_PREFIX + '/images/' + imageId)
    .then(function(r) { return r.json(); })
    .then(function(data) {
        document.getElementById('modal-thumb').src = API_PREFIX + '/images/' + imageId + '/thumbnail';
        document.getElementById('modal-filename').textContent = data.current_filename || data.original_filename || '-';

        var statusEl = document.getElementById('modal-status');
        statusEl.textContent = data.status || '-';
        statusEl.className = 'badge badge-' + (data.status || '');

        var approveBtn = document.getElementById('modal-approve-btn');
        if (approveBtn) {
            approveBtn.style.display = (data.status === 'proposed') ? '' : 'none';
        }

        var skipBtn = document.getElementById('modal-skip-btn');
        if (skipBtn) {
            skipBtn.style.display = (data.status === 'proposed') ? '' : 'none';
        }

        var revertBtn = document.getElementById('modal-revert-btn');
        if (revertBtn) {
            revertBtn.style.display = (data.status === 'renamed') ? '' : 'none';
        }

        var trashBtn = document.getElementById('modal-trash-btn');
        if (trashBtn) {
            trashBtn.style.display = (data.status !== 'trashed') ? '' : 'none';
        }

        var queueBtn = document.getElementById('modal-queue-btn');
        if (queueBtn) {
            var inQueue = data.in_queue === 1 || data.in_queue === true;
            queueBtn.textContent = inQueue ? 'Remove from Queue' : 'Add to Queue';
            queueBtn.className = inQueue ? 'btn btn-sm btn-warning' : 'btn btn-sm';
        }

        document.getElementById('modal-date').textContent = data.exif_date || '-';
        document.getElementById('modal-camera').textContent = data.camera_model || '-';
        document.getElementById('modal-location').textContent = data.location_name || '-';
        document.getElementById('modal-original').textContent = data.original_filename || '-';
        document.getElementById('modal-size').textContent = _formatFileSize(data.file_size);

        if (data.gps_lat && data.gps_lon) {
            document.getElementById('modal-gps').textContent = data.gps_lat.toFixed(5) + ', ' + data.gps_lon.toFixed(5);
        } else {
            document.getElementById('modal-gps').textContent = '-';
        }

        if (data.confidence_score != null && data.status !== 'completed') {
            var pct = Math.round(data.confidence_score * 100);
            var level = pct >= 70 ? 'high' : (pct >= 40 ? 'medium' : 'low');
            document.getElementById('modal-confidence').innerHTML = '<span class="confidence confidence-' + level + '">' + pct + '%</span>';
        } else {
            document.getElementById('modal-confidence').textContent = '-';
        }

        var ctxSection = document.getElementById('modal-context-section');
        if (data.processing_context) {
            document.getElementById('modal-context').textContent = data.processing_context;
            ctxSection.style.display = '';
        } else {
            ctxSection.style.display = 'none';
        }

        var descSection = document.getElementById('modal-description-section');
        if (data.vision_description) {
            document.getElementById('modal-description').textContent = data.vision_description;
            descSection.style.display = '';
        } else {
            descSection.style.display = 'none';
        }

        var exifSection = document.getElementById('modal-exif-section');
        if (data.exif_raw && typeof data.exif_raw === 'object' && Object.keys(data.exif_raw).length > 0) {
            var cleaned = _cleanExif(data.exif_raw);
            if (Object.keys(cleaned).length > 0) {
                document.getElementById('modal-exif-raw').textContent = _formatExif(cleaned);
                exifSection.style.display = '';
                exifSection.removeAttribute('open');
            } else {
                exifSection.style.display = 'none';
            }
        } else {
            exifSection.style.display = 'none';
        }

        var sidecarSection = document.getElementById('modal-sidecar-section');
        if (data.sidecar_path) {
            var sidecarObj = {};
            sidecarObj['Sidecar File'] = data.sidecar_path.split('/').pop();
            if (data.vision_description) sidecarObj['Description'] = data.vision_description;
            var tags = data.ai_tags;
            if (tags && typeof tags === 'string') {
                try { tags = JSON.parse(tags); } catch(e) {}
            }
            if (tags && Array.isArray(tags) && tags.length > 0) {
                sidecarObj['Tags'] = tags.join(', ');
            }
            if (data.exif_date) {
                sidecarObj['Date Original'] = data.exif_date;
                sidecarObj['Date Created'] = data.exif_date;
            }
            document.getElementById('modal-sidecar-raw').textContent = _formatExif(sidecarObj);
            sidecarSection.style.display = '';
            sidecarSection.removeAttribute('open');
        } else {
            sidecarSection.style.display = 'none';
        }

        var downloadBtn = document.getElementById('modal-download-btn');
        if (downloadBtn) downloadBtn.href = API_PREFIX + '/images/' + imageId + '/file?download=true';

        var modal = document.getElementById('image-modal');
        modal.dataset.imageId = imageId;
        modal.dataset.context = data.processing_context || '';
        modal.style.display = 'flex';
        _updateModalNav();
    })
    .catch(function() { showToast('Failed to load image details', 'error'); });
}

function closeImageModal() {
    document.getElementById('image-modal').style.display = 'none';
    document.getElementById('modal-thumb').src = '';
}

function closeModal(event) {
    if (event.target.id === 'image-modal') closeImageModal();
}

function modalPrev() {
    if (_modalIndex > 0) {
        _modalIndex--;
        openImageModal(_modalImageIds[_modalIndex], true);
    }
}

function modalNext() {
    if (_modalIndex < _modalImageIds.length - 1) {
        _modalIndex++;
        openImageModal(_modalImageIds[_modalIndex], true);
    }
}

document.addEventListener('keydown', function(e) {
    var modal = document.getElementById('image-modal');
    if (!modal || modal.style.display === 'none') return;
    if (e.key === 'Escape') closeImageModal();
    if (e.key === 'ArrowLeft') modalPrev();
    if (e.key === 'ArrowRight') modalNext();
});

function modalReprocess() {
    var modal = document.getElementById('image-modal');
    var imageId = modal.dataset.imageId;
    var prevContext = modal.dataset.context || '';
    showContextModal('Reprocess with Context', prevContext, function(context) {
        var opts = { method: 'POST' };
        if (context !== null) {
            opts.headers = { 'Content-Type': 'application/json' };
            opts.body = JSON.stringify({ context: context });
        }
        fetch(API_PREFIX + '/images/' + imageId + '/process', opts)
        .then(function(r) { return r.json(); })
        .then(function() {
            showToast('Reprocessing...');
            closeImageModal();
        })
        .catch(function() { showToast('Request failed', 'error'); });
    });
}

function modalRevert() {
    var imageId = document.getElementById('image-modal').dataset.imageId;
    fetch(API_PREFIX + '/images/' + imageId + '/revert', { method: 'POST' })
    .then(function(r) { return r.json().then(function(data) { return { ok: r.ok, data: data }; }); })
    .then(function(result) {
        if (result.ok && result.data.status === 'reverted') {
            showToast('Reverted: ' + (result.data.restored_name || ''));
            closeImageModal();
            setTimeout(function() { window.location.reload(); }, 500);
        } else {
            showToast(result.data.detail || 'Revert failed', 'error');
        }
    })
    .catch(function() { showToast('Request failed', 'error'); });
}

function modalApprove() {
    var imageId = document.getElementById('image-modal').dataset.imageId;
    fetch(API_PREFIX + '/images/' + imageId + '/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: null })
    })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(result) {
        if (result.ok && result.data.status === 'renamed') {
            showToast('Approved: ' + (result.data.new_name || ''));
            closeImageModal();
            setTimeout(function() { window.location.reload(); }, 500);
        } else {
            showToast(result.data.detail || 'Approve failed', 'error');
        }
    })
    .catch(function() { showToast('Request failed', 'error'); });
}

function modalSkip() {
    var imageId = document.getElementById('image-modal').dataset.imageId;
    fetch(API_PREFIX + '/images/' + imageId + '/skip', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function() {
        showToast('Skipped');
        closeImageModal();
        var card = document.getElementById('review-card-' + imageId);
        if (card) card.remove();
        _removeRow(parseInt(imageId));
    })
    .catch(function() { showToast('Request failed', 'error'); });
}

function modalTrash() {
    var isWs = typeof API_PREFIX !== 'undefined' && API_PREFIX.includes('workspace');
    if (isWs) {
        if (!window.DESTRUCTIVE_WORKSPACE) { showDestructiveModal('workspace'); return; }
    } else {
        if (!window.DESTRUCTIVE_LIBRARY) { showDestructiveModal('library'); return; }
    }
    var imageId = document.getElementById('image-modal').dataset.imageId;
    var title = isWs ? 'Permanently delete?' : 'Delete file?';
    var msg = isWs
        ? 'This file will be permanently deleted. This cannot be undone.'
        : 'This file will be moved to the trash. You can restore it or permanently delete it from the Trashed tab.';
    showConfirm(title, msg, 'Delete', function() {
        var url = isWs
            ? API_PREFIX + '/images/' + imageId
            : API_PREFIX + '/images/' + imageId + '/trash';
        var method = isWs ? 'DELETE' : 'POST';
        fetch(url, { method: method })
        .then(function(r) { return r.json(); })
        .then(function() {
            showToast(isWs ? 'Deleted permanently' : 'Moved to trash');
            closeImageModal();
            _removeRow(parseInt(imageId));
            var card = document.querySelector('.search-result-card[data-image-id="' + imageId + '"]');
            if (card) card.remove();
            var reviewCard = document.getElementById('review-card-' + imageId);
            if (reviewCard) reviewCard.remove();
        })
        .catch(function() { showToast('Failed to delete image', 'error'); });
    });
}

/* ── Queue Functions ──────────────────────────────────────── */

function modalToggleQueue() {
    var imageId = document.getElementById('image-modal').dataset.imageId;
    fetch(API_PREFIX + '/images/' + imageId + '/queue', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        var btn = document.getElementById('modal-queue-btn');
        if (data.in_queue) {
            btn.textContent = 'Remove from Queue';
            btn.className = 'btn btn-sm btn-warning';
            showToast('Added to queue');
        } else {
            btn.textContent = 'Add to Queue';
            btn.className = 'btn btn-sm';
            showToast('Removed from queue');
        }
        _updateQueueBadge(imageId, data.in_queue);
    })
    .catch(function() { showToast('Request failed', 'error'); });
}

function bulkQueueAdd() {
    var ids = getSelectedIds();
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }
    fetch(API_PREFIX + '/images/queue-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: ids, action: 'add' })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        showToast('Added ' + (data.updated || 0) + ' images to queue');
        ids.forEach(function(id) { _updateQueueBadge(id, true); });
    })
    .catch(function() { showToast('Failed to add to queue', 'error'); });
}

function bulkQueueRemove() {
    var ids = getSelectedIds();
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }
    fetch(API_PREFIX + '/images/queue-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: ids, action: 'remove' })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        showToast('Removed ' + (data.updated || 0) + ' images from queue');
        _removeRows(ids);
        _decrementTabCount(ids.length);
    })
    .catch(function() { showToast('Failed to remove from queue', 'error'); });
}

function clearQueue() {
    fetch(API_PREFIX + '/images/queue-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: [], action: 'clear' })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        showToast('Queue cleared (' + (data.updated || 0) + ' images)');
        if (window.location.search.indexOf('status=queued') !== -1) {
            window.location.reload();
        }
    })
    .catch(function() { showToast('Failed to clear queue', 'error'); });
}

function _updateQueueBadge(imageId, inQueue) {
    var statusCell = document.getElementById('queue-status-' + imageId);
    if (!statusCell) return;
    var existing = statusCell.querySelector('.badge-queue');
    if (inQueue && !existing) {
        var badge = document.createElement('span');
        badge.className = 'badge badge-queue';
        badge.textContent = 'Q';
        // Insert after the status badge
        var statusBadge = statusCell.querySelector('.badge');
        if (statusBadge) {
            statusBadge.insertAdjacentElement('afterend', badge);
        } else {
            statusCell.appendChild(badge);
        }
    } else if (!inQueue && existing) {
        existing.remove();
    }
}

function openFullImage() {
    var imageId = document.getElementById('image-modal').dataset.imageId;
    window.open(API_PREFIX + '/images/' + imageId + '/viewer', '_blank');
}

// ── Folder Tree (Dashboard) ──────────────────────────────────

var _folderTreeLoaded = false;

// ── Idle Card: independent Schedule / Folders toggles ─────────

var _foldersVisible = false;
var _scheduleVisible = false;
var _contextVisible = false;

function _syncIdleCard() {
    var body = document.getElementById('idle-body');
    var card = document.getElementById('progress-idle');
    if (!body || !card) return;
    var anyVisible = _foldersVisible || _scheduleVisible || _contextVisible;
    body.style.display = anyVisible ? '' : 'none';
    if (anyVisible) card.classList.add('idle-open');
    else card.classList.remove('idle-open');
}

function toggleIdleFolders() {
    var panel = document.getElementById('folder-tree-panel');
    var btn = document.getElementById('folders-btn');
    if (!panel || !btn) return;
    _foldersVisible = !_foldersVisible;
    panel.style.display = _foldersVisible ? '' : 'none';
    btn.textContent = _foldersVisible ? 'Hide Folders' : 'Show Folders';
    if (_foldersVisible && !_folderTreeLoaded) {
        initFolderTree();
        _folderTreeLoaded = true;
    }
    _syncIdleCard();
}

function refreshFolderTree() {
    _folderTreeLoaded = false;
    var container = document.getElementById('folder-tree-container');
    if (container) container.innerHTML = '<div class="folder-tree-loading">Loading folder tree...</div>';
    initFolderTree();
    _folderTreeLoaded = true;
}

var _folderSaveTimer = null;
function autoSaveExcludedFolders() {
    if (_folderSaveTimer) clearTimeout(_folderSaveTimer);
    _folderSaveTimer = setTimeout(function() {
        var input = document.getElementById('excluded_folders');
        if (!input) return;
        fetch('/api/settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ settings: { excluded_folders: input.value } })
        })
        .then(function(r) { return r.json(); })
        .then(function() { showToast('Folder selections saved'); })
        .catch(function() { showToast('Failed to save folder selections', 'error'); });
    }, 500);
}

function initFolderTree() {
    var container = document.getElementById('folder-tree-container');
    if (!container) return;

    fetch('/api/folders')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var tree = data.tree || [];
            if (tree.length === 0) {
                container.style.display = 'none';
                var empty = document.getElementById('folder-tree-empty');
                if (empty) empty.style.display = '';
                return;
            }

            // Parse currently excluded folders from the hidden input
            var excludedInput = document.getElementById('excluded_folders');
            var excluded = new Set();
            if (excludedInput && excludedInput.value) {
                try {
                    var arr = JSON.parse(excludedInput.value);
                    if (Array.isArray(arr)) {
                        arr.forEach(function(p) { excluded.add(p.replace(/^\/|\/$/g, '')); });
                    }
                } catch (e) { /* ignore bad JSON */ }
            }

            // Render the tree
            container.innerHTML = '';
            var treeEl = document.createElement('div');
            treeEl.className = 'folder-tree';
            var ul = renderFolderNodes(tree, excluded);
            treeEl.appendChild(ul);
            container.appendChild(treeEl);

            // Apply initial excluded state (propagate parent exclusions to children)
            applyExcludedState(excluded);

            // Update indeterminate states from bottom up
            updateIndeterminateStates(excluded);

            // Update visual styling
            updateFolderNameStyles();
        })
        .catch(function() {
            container.innerHTML = '<p class="text-muted">Could not load folder tree.</p>';
        });
}

function renderFolderNodes(nodes, excluded) {
    var ul = document.createElement('ul');

    nodes.forEach(function(node) {
        var li = document.createElement('li');

        var row = document.createElement('div');
        row.className = 'folder-node';

        // Toggle button (expand/collapse)
        var toggle = document.createElement('button');
        toggle.className = 'folder-toggle' + (node.children.length === 0 ? ' leaf' : '');
        toggle.type = 'button';
        toggle.innerHTML = '&#9660;';
        toggle.addEventListener('click', function() {
            var childUl = li.querySelector(':scope > ul');
            if (!childUl) return;
            var isCollapsed = childUl.style.display === 'none';
            childUl.style.display = isCollapsed ? '' : 'none';
            toggle.classList.toggle('collapsed', !isCollapsed);
        });

        // Checkbox
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'folder-checkbox';
        cb.dataset.path = node.path;
        cb.checked = !excluded.has(node.path);
        cb.addEventListener('change', function() {
            onFolderCheckboxChange(cb);
        });

        // Folder name label
        var nameSpan = document.createElement('span');
        nameSpan.className = 'folder-name';
        if (node.path === '__root_files__') nameSpan.classList.add('folder-name-virtual');
        nameSpan.textContent = node.name;
        nameSpan.title = node.path;

        row.appendChild(toggle);
        row.appendChild(cb);
        row.appendChild(nameSpan);
        li.appendChild(row);

        // Recurse for children
        if (node.children.length > 0) {
            var childUl = renderFolderNodes(node.children, excluded);
            li.appendChild(childUl);
        }

        ul.appendChild(li);
    });

    return ul;
}

function onFolderCheckboxChange(cb) {
    var isChecked = cb.checked;

    // Propagate down: check/uncheck all descendant checkboxes
    var parentLi = cb.closest('li');
    if (parentLi) {
        var descendantCbs = parentLi.querySelectorAll('ul .folder-checkbox');
        descendantCbs.forEach(function(child) {
            child.checked = isChecked;
            child.indeterminate = false;
        });
    }

    // Propagate up: update ancestor indeterminate states only
    _updateAncestorStates(cb);

    // Update the hidden input
    syncExcludedFoldersInput();

    // Update visual styling (strikethrough for excluded)
    updateFolderNameStyles();

    // Auto-save to server (dashboard mode)
    autoSaveExcludedFolders();
}

function updateIndeterminateStates(excluded) {
    // Walk the tree bottom-up: for each parent, check its direct children
    var allCbs = document.querySelectorAll('.folder-checkbox');
    var cbArray = Array.from(allCbs).reverse();

    cbArray.forEach(function(cb) {
        var parentLi = cb.closest('li');
        if (!parentLi) return;

        var childUl = parentLi.querySelector(':scope > ul');
        if (!childUl) return;  // Leaf node

        var childCbs = childUl.querySelectorAll(':scope > li > .folder-node > .folder-checkbox');
        if (childCbs.length === 0) return;

        var checkedCount = 0;
        var indeterminateCount = 0;
        childCbs.forEach(function(c) {
            if (c.checked) checkedCount++;
            if (c.indeterminate) indeterminateCount++;
        });

        if (checkedCount === childCbs.length) {
            cb.indeterminate = false;
            cb.checked = true;
        } else if (checkedCount === 0 && indeterminateCount === 0) {
            // All children fully unchecked
            if (excluded && excluded.has(cb.dataset.path)) {
                // Parent was explicitly in the saved exclusion list — keep unchecked
                cb.indeterminate = false;
                cb.checked = false;
            } else {
                // Children excluded but parent wasn't — indeterminate
                // (folder itself may contain files the user wants to keep)
                cb.indeterminate = true;
                cb.checked = false;
            }
        } else {
            // Mixed checked/unchecked/indeterminate children
            cb.indeterminate = true;
            cb.checked = false;
        }
    });
}

function _updateAncestorStates(startCb) {
    // Walk UP from the changed checkbox to root, updating each ancestor.
    // The clicked checkbox itself is NOT modified — only its ancestors.
    var li = startCb.closest('li');
    if (!li) return;
    var parentUl = li.parentElement;
    while (parentUl) {
        var parentLi = parentUl.closest('li');
        if (!parentLi) break;
        var parentCb = parentLi.querySelector(':scope > .folder-node > .folder-checkbox');
        if (!parentCb) break;

        var childUl = parentLi.querySelector(':scope > ul');
        if (!childUl) break;
        var childCbs = childUl.querySelectorAll(':scope > li > .folder-node > .folder-checkbox');

        var checkedCount = 0, indeterminateCount = 0;
        childCbs.forEach(function(c) {
            if (c.checked) checkedCount++;
            if (c.indeterminate) indeterminateCount++;
        });

        if (checkedCount === childCbs.length) {
            parentCb.indeterminate = false;
            parentCb.checked = true;
        } else {
            // Any mix (including all-unchecked) → indeterminate
            // User must directly click a parent to fully exclude it
            parentCb.indeterminate = true;
            parentCb.checked = false;
        }

        parentUl = parentLi.parentElement;
    }
}

function syncExcludedFoldersInput() {
    var excludedInput = document.getElementById('excluded_folders');
    if (!excludedInput) return;

    var excluded = [];
    document.querySelectorAll('.folder-checkbox').forEach(function(cb) {
        // A folder is excluded if unchecked AND not indeterminate
        if (!cb.checked && !cb.indeterminate) {
            excluded.push(cb.dataset.path);
        }
    });

    // Minimize: remove children whose parent is already excluded
    var excludedSet = new Set(excluded);
    var minimal = excluded.filter(function(path) {
        // Root "." is the top-level parent, always kept
        if (path === '.') return true;
        // If root is excluded, all children are redundant
        if (excludedSet.has('.')) return false;
        var parts = path.split('/');
        for (var i = 1; i < parts.length; i++) {
            var parent = parts.slice(0, i).join('/');
            if (excludedSet.has(parent)) return false;
        }
        return true;
    });

    excludedInput.value = minimal.length > 0 ? JSON.stringify(minimal) : '';
}

function applyExcludedState(excluded) {
    if (excluded.size === 0) return;

    // If root "." is excluded, uncheck everything
    if (excluded.has('.')) {
        document.querySelectorAll('.folder-checkbox').forEach(function(cb) {
            cb.checked = false;
        });
        return;
    }

    document.querySelectorAll('.folder-checkbox').forEach(function(cb) {
        var path = cb.dataset.path;
        if (path === '.') return;  // Root handled by indeterminate logic
        var parts = path.split('/');
        // Check if this path or any ancestor is excluded
        for (var i = 1; i <= parts.length; i++) {
            var ancestor = parts.slice(0, i).join('/');
            if (excluded.has(ancestor)) {
                cb.checked = false;
                // Also uncheck all descendants
                var li = cb.closest('li');
                if (li) {
                    li.querySelectorAll('ul .folder-checkbox').forEach(function(child) {
                        child.checked = false;
                    });
                }
                break;
            }
        }
    });
}

function updateFolderNameStyles() {
    document.querySelectorAll('.folder-checkbox').forEach(function(cb) {
        var nameSpan = cb.parentElement.querySelector('.folder-name');
        if (nameSpan) {
            nameSpan.classList.toggle('excluded', !cb.checked && !cb.indeterminate);
        }
    });
}

// ── Schedule (Dashboard) ─────────────────────────────────────

function toggleSchedulePanel() {
    var panel = document.getElementById('schedule-panel');
    var btn = document.getElementById('schedule-btn');
    if (!panel || !btn) return;
    _scheduleVisible = !_scheduleVisible;
    panel.style.display = _scheduleVisible ? '' : 'none';
    btn.textContent = _scheduleVisible ? 'Hide Schedule' : 'Schedule';
    _syncIdleCard();
}

function saveSchedule() {
    var enabled = document.getElementById('dash-schedule-enabled');
    var start = document.getElementById('dash-schedule-start');
    var end = document.getElementById('dash-schedule-end');
    if (!enabled || !start || !end) return;

    var settings = {
        schedule_enabled: enabled.checked ? 'true' : 'false',
        schedule_start: start.value,
        schedule_end: end.value
    };

    fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: settings })
    })
    .then(function(r) { return r.json(); })
    .then(function() {
        if (enabled.checked) {
            showToast('Schedule enabled: ' + start.value + ' – ' + end.value);
        } else {
            showToast('Schedule disabled');
        }
    })
    .catch(function() { showToast('Failed to save schedule', 'error'); });
}

function updateScheduleStatus(data) {
    var statusBar = document.getElementById('schedule-status');
    var statusText = document.getElementById('schedule-status-text');
    if (!statusBar || !statusText) return;

    var schedule = data.schedule;
    if (!schedule || !schedule.enabled) {
        statusBar.style.display = 'none';
        return;
    }

    statusBar.style.display = '';
    if (schedule.in_window) {
        statusText.textContent = 'Scheduled: processing until ' + schedule.end +
            (schedule.next_change ? ' (' + schedule.next_change + ')' : '');
    } else {
        statusText.textContent = 'Scheduled: paused until ' + schedule.start +
            (schedule.next_change ? ' (' + schedule.next_change + ')' : '');
    }
}

// ── Advanced Scan (with Context) ─────────────────────────────

function toggleAdvancedScan() {
    _contextVisible = !_contextVisible;
    var panel = document.getElementById('advanced-scan-panel');
    var btn = document.getElementById('advanced-scan-btn');
    var scanNowBtn = document.getElementById('scan-now-btn');

    if (_contextVisible) {
        panel.style.display = '';
        document.getElementById('idle-body').style.display = '';
        document.getElementById('progress-idle').classList.add('idle-open');
        document.getElementById('advanced-scan-context').value = '';
        document.getElementById('advanced-scan-context').focus();
        btn.textContent = 'Cancel Context';
        btn.className = 'btn btn-sm';
        scanNowBtn.disabled = true;
        scanNowBtn.style.opacity = '0.4';
    } else {
        _exitContextMode();
    }
}

function _exitContextMode() {
    _contextVisible = false;
    var panel = document.getElementById('advanced-scan-panel');
    var btn = document.getElementById('advanced-scan-btn');
    var scanNowBtn = document.getElementById('scan-now-btn');

    panel.style.display = 'none';
    btn.textContent = 'Scan with Context';
    btn.className = 'btn btn-sm btn-context';
    scanNowBtn.disabled = false;
    scanNowBtn.style.opacity = '';
    _syncIdleCard();
}

function hideAdvancedScan() {
    _exitContextMode();
}

function scanWithContext() {
    var input = document.getElementById('advanced-scan-context');
    var context = input ? input.value.trim() : '';
    if (!context) {
        showToast('Please enter context or use the regular Scan Now button', 'error');
        return;
    }
    fetch('/api/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ context: context })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        showToast(data.message || 'Scan started with context');
        hideAdvancedScan();
    })
    .catch(function() { showToast('Scan failed', 'error'); });
}

function stopProcessing() {
    var btn = document.getElementById('stop-processing-btn');
    if (btn) { btn.textContent = 'Stopping...'; btn.disabled = true; }
    fetch('/api/scan/stop', { method: 'POST' })
        .then(function(r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function() { showToast('Stop requested — finishing current image...'); updateDashboard(); })
        .catch(function() { showToast('Failed to stop processing', 'error'); });
}

function resumeProcessing() {
    var btn = document.getElementById('stop-processing-btn');
    if (btn) { btn.textContent = 'Resuming...'; btn.disabled = true; }
    fetch('/api/scan/resume', { method: 'POST' })
        .then(function(r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function() { showToast('Processing resumed'); updateDashboard(); })
        .catch(function() { showToast('Failed to resume processing', 'error'); });
}

function retryAllErrors() {
    fetch('/api/images/retry-all-errors', { method: 'POST' })
        .then(function(r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function(data) {
            showToast('Retrying ' + (data.count || 0) + ' error image(s)');
            setTimeout(function() { window.location.reload(); }, 600);
        })
        .catch(function() { showToast('Failed to retry errors', 'error'); });
}

// ── Settings: Processing Modes ──────────────────────────────

function toggleCatalogueMode() {
    var cat = document.getElementById('catalogue_mode');
    var isCat = cat && cat.checked;
    ['process_rename', 'process_write_description', 'process_write_tags'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.disabled = isCat || window.PHOTOS_READONLY;
    });
    var error = document.getElementById('processing-mode-error');
    if (error) error.style.display = isCat ? 'none' : (validateProcessingModes() ? 'none' : '');
    toggleRenameCards();
}

function validateProcessingModes() {
    var cat = document.getElementById('catalogue_mode');
    if (cat && cat.checked) return true;

    var rename = document.getElementById('process_rename');
    var desc = document.getElementById('process_write_description');
    var tags = document.getElementById('process_write_tags');
    var error = document.getElementById('processing-mode-error');
    if (!rename || !desc || !tags) return true;

    var anyChecked = rename.checked || desc.checked || tags.checked;
    if (error) error.style.display = anyChecked ? 'none' : '';

    // Show/hide rename-specific cards
    toggleRenameCards();

    return anyChecked;
}

function toggleRenameCards() {
    var rename = document.getElementById('process_rename');
    if (!rename) return;
    var cat = document.getElementById('catalogue_mode');
    var renameMode = document.getElementById('card-rename-mode');
    var filenameTemplate = document.getElementById('card-filename-template');
    var show = rename.checked && !(cat && cat.checked);
    if (renameMode) renameMode.style.display = show ? '' : 'none';
    if (filenameTemplate) filenameTemplate.style.display = show ? '' : 'none';
}

// ── Settings: Security ──────────────────────────────────────

function toggleAuthFields() {
    var enabled = document.getElementById('auth_enabled');
    var fields = document.getElementById('auth-fields');
    if (enabled && fields) {
        fields.style.display = enabled.checked ? '' : 'none';
    }
}

function settingsCheckPassword() {
    var password = document.getElementById('settings_auth_pass').value;
    var confirm = document.getElementById('settings_auth_pass_confirm').value;
    var strengthDiv = document.getElementById('settings-strength');
    var fillDiv = document.getElementById('settings-strength-fill');
    var labelSpan = document.getElementById('settings-strength-label');
    var matchError = document.getElementById('settings-match-error');

    if (!password) {
        if (strengthDiv) strengthDiv.style.display = 'none';
        if (matchError) matchError.style.display = 'none';
        return;
    }

    if (strengthDiv) strengthDiv.style.display = '';
    var strength = _settingsPasswordStrength(password);
    if (fillDiv) {
        fillDiv.className = 'strength-fill ' + strength.level;
        fillDiv.style.width = strength.percent + '%';
    }
    if (labelSpan) {
        labelSpan.textContent = strength.label;
        labelSpan.className = 'strength-label strength-' + strength.level;
    }

    if (matchError) {
        matchError.style.display = (confirm && password !== confirm) ? '' : 'none';
    }
}

function _settingsPasswordStrength(password) {
    var score = 0;
    var len = password.length;
    if (len >= 4) score++;
    if (len >= 8) score++;
    if (len >= 12) score++;
    if (len >= 16) score++;
    if (/[a-z]/.test(password)) score++;
    if (/[A-Z]/.test(password)) score++;
    if (/[0-9]/.test(password)) score++;
    if (/[^a-zA-Z0-9]/.test(password)) score++;

    if (score <= 3) return { score: score, level: 'weak', label: 'Weak', percent: 33 };
    if (score <= 5) return { score: score, level: 'medium', label: 'Medium', percent: 66 };
    return { score: score, level: 'strong', label: 'Strong', percent: 100 };
}

// ── Settings: Save ──────────────────────────────────────────

function saveSettings() {
    // Validate processing modes
    if (!validateProcessingModes()) {
        showToast('At least one processing mode must be enabled', 'error');
        return;
    }

    const getValue = (id) => {
        const el = document.getElementById(id);
        if (!el) return undefined;
        if (el.type === 'checkbox') return el.checked ? 'true' : 'false';
        if (el.type === 'radio') {
            const checked = document.querySelector(`input[name="${el.name}"]:checked`);
            return checked ? checked.value : el.value;
        }
        return el.value;
    };

    const settings = {};
    const fields = [
        'ollama_host', 'vision_model', 'llm_model',
        'catalogue_mode',
        'process_rename', 'process_write_description', 'process_write_tags',
        'filename_template', 'filename_case', 'max_filename_len',
        'confidence_threshold',
        'use_exif_date', 'use_gps', 'gps_detail',
        'watch_mode', 'process_subdirs', 'concurrent_workers',
        'schedule_enabled', 'schedule_start', 'schedule_end',
        'thumbnail_max_size', 'thumbnail_quality',
        'destructive_mode_library', 'destructive_mode_workspace',
        'dashboard_showcase', 'dashboard_showcase_tag', 'dashboard_showcase_interval',
        'dashboard_showcase_kenburns',
        'dashboard_mosaic_speed',
        'dashboard_crossfade_speed',
    ];

    fields.forEach(f => {
        const v = getValue(f);
        if (v !== undefined) settings[f] = v;
    });

    // Handle rename_mode radio group
    const modeEl = document.querySelector('input[name="rename_mode"]:checked');
    if (modeEl) settings['rename_mode'] = modeEl.value;

    // Handle auth settings
    const authEnabled = document.getElementById('auth_enabled');
    if (authEnabled) {
        if (authEnabled.checked) {
            const user = document.getElementById('settings_auth_user');
            const pass = document.getElementById('settings_auth_pass');
            const passConfirm = document.getElementById('settings_auth_pass_confirm');
            if (user) settings['basic_auth_user'] = user.value.trim();
            // Only send password if non-empty and matching confirm
            if (pass && pass.value) {
                if (passConfirm && pass.value !== passConfirm.value) {
                    showToast('Passwords do not match', 'error');
                    return;
                }
                settings['basic_auth_pass'] = pass.value;
            }
        } else {
            settings['basic_auth_user'] = '';
            settings['basic_auth_pass'] = '';
        }
    }

    fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: settings })
    })
    .then(r => r.json())
    .then(() => {
        showToast('Settings saved');
        setTimeout(function() { location.reload(); }, 600);
    })
    .catch(() => showToast('Failed to save settings', 'error'));
}

// ── Prompt Library ──────────────────────────────────────────

// State: track loaded prompts and which is currently viewed per stage
var _promptsByStage = {};  // { vision: [...], context_injection: [...] }
var _viewedPromptId = {};  // { vision: 123, context_injection: 456 }

function loadPrompts(stage) {
    fetch('/api/prompts?stage=' + encodeURIComponent(stage))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var prompts = data.prompts || [];
            _promptsByStage[stage] = prompts;
            var sel = document.getElementById('prompt-select-' + stage);
            if (!sel) return;
            sel.innerHTML = '';
            prompts.forEach(function(p) {
                var opt = document.createElement('option');
                opt.value = p.id;
                opt.textContent = p.name + (p.is_active ? ' (active)' : '') + (p.is_default ? ' [default]' : '');
                sel.appendChild(opt);
            });
            // Select the active one
            var active = prompts.find(function(p) { return p.is_active; });
            if (active) {
                sel.value = active.id;
                viewPrompt(active.id);
            } else if (prompts.length > 0) {
                sel.value = prompts[0].id;
                viewPrompt(prompts[0].id);
            }
        })
        .catch(function() { showToast('Failed to load prompts', 'error'); });
}

function viewPrompt(promptId) {
    if (!promptId) return;
    fetch('/api/prompts/' + promptId)
        .then(function(r) { return r.json(); })
        .then(function(p) {
            var stage = p.stage;
            _viewedPromptId[stage] = p.id;
            var editor = document.getElementById('prompt-editor-' + stage);
            var label = document.getElementById('prompt-label-' + stage);
            var saveBtn = document.getElementById('prompt-save-btn-' + stage);
            var deleteBtn = document.getElementById('prompt-delete-btn-' + stage);
            if (editor) {
                editor.value = p.content;
                editor.readOnly = !!p.is_default;
            }
            if (label) {
                label.textContent = p.name + (p.is_default ? ' (default — read-only)' : '');
            }
            if (saveBtn) saveBtn.style.display = p.is_default ? 'none' : '';
            if (deleteBtn) deleteBtn.style.display = (p.is_default || p.is_active) ? 'none' : '';
        })
        .catch(function() { showToast('Failed to load prompt', 'error'); });
}

function activateSelectedPrompt(stage) {
    var sel = document.getElementById('prompt-select-' + stage);
    if (!sel || !sel.value) return;
    fetch('/api/prompts/' + sel.value + '/activate', { method: 'POST' })
        .then(function(r) {
            if (!r.ok) return r.json().then(function(d) { throw new Error(d.detail || 'Failed'); });
            return r.json();
        })
        .then(function() {
            showToast('Prompt activated');
            loadPrompts(stage);
        })
        .catch(function(e) { showToast(e.message || 'Failed to activate', 'error'); });
}

function savePrompt(stage) {
    var promptId = _viewedPromptId[stage];
    if (!promptId) return;
    var editor = document.getElementById('prompt-editor-' + stage);
    if (!editor) return;

    fetch('/api/prompts/' + promptId, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: editor.value })
    })
    .then(function(r) {
        if (!r.ok) return r.json().then(function(d) { throw new Error(d.detail || 'Validation failed'); });
        return r.json();
    })
    .then(function() {
        showToast('Prompt saved');
        loadPrompts(stage);
    })
    .catch(function(e) { showToast(e.message || 'Failed to save', 'error'); });
}

function showAddPromptForm(stage) {
    var form = document.getElementById('prompt-add-form-' + stage);
    if (form) form.style.display = '';
    var err = document.getElementById('prompt-add-error-' + stage);
    if (err) err.style.display = 'none';
}

function hideAddPromptForm(stage) {
    var form = document.getElementById('prompt-add-form-' + stage);
    if (form) form.style.display = 'none';
}

function createPrompt(stage) {
    var nameEl = document.getElementById('prompt-add-name-' + stage);
    var contentEl = document.getElementById('prompt-add-content-' + stage);
    var errEl = document.getElementById('prompt-add-error-' + stage);
    if (!nameEl || !contentEl) return;

    var name = nameEl.value.trim();
    var content = contentEl.value;
    if (!name) {
        if (errEl) { errEl.textContent = 'Name is required'; errEl.style.display = ''; }
        return;
    }
    if (!content.trim()) {
        if (errEl) { errEl.textContent = 'Content is required'; errEl.style.display = ''; }
        return;
    }

    fetch('/api/prompts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, stage: stage, content: content })
    })
    .then(function(r) {
        if (!r.ok) return r.json().then(function(d) { throw new Error(d.detail || 'Validation failed'); });
        return r.json();
    })
    .then(function() {
        showToast('Prompt created');
        hideAddPromptForm(stage);
        nameEl.value = '';
        contentEl.value = '';
        loadPrompts(stage);
    })
    .catch(function(e) {
        if (errEl) { errEl.textContent = e.message || 'Failed to create'; errEl.style.display = ''; }
    });
}

function deletePromptById(stage) {
    var promptId = _viewedPromptId[stage];
    if (!promptId) return;
    showConfirm('Delete Prompt', 'Are you sure you want to delete this custom prompt?', 'Delete', function() {
        fetch('/api/prompts/' + promptId, { method: 'DELETE' })
            .then(function(r) {
                if (!r.ok) return r.json().then(function(d) { throw new Error(d.detail || 'Failed'); });
                return r.json();
            })
            .then(function() {
                showToast('Prompt deleted');
                loadPrompts(stage);
            })
            .catch(function(e) { showToast(e.message || 'Failed to delete', 'error'); });
    });
}

function _copyText(text, msg) {
    msg = msg || 'Copied to clipboard';
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function() {
            showToast(msg);
        }).catch(function() {
            _copyTextFallback(text, msg);
        });
    } else {
        _copyTextFallback(text, msg);
    }
}

function _copyTextFallback(text, msg) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;left:-9999px';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    showToast(msg);
}

function copyPromptToClipboard(stage) {
    var editor = document.getElementById('prompt-editor-' + stage);
    if (!editor) return;
    _copyText(editor.value, 'Copied to clipboard');
}

function downloadPromptMarkdown(stage) {
    var promptId = _viewedPromptId[stage];
    if (!promptId) return;
    window.location.href = '/api/prompts/' + promptId + '/export';
}

function showAiHelperModal() {
    fetch('/api/prompts/ai-helper')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var titleEl = document.getElementById('confirm-title');
            var msgEl = document.getElementById('confirm-message');
            var okBtn = document.getElementById('confirm-ok-btn');

            if (titleEl) titleEl.textContent = 'Create with AI';
            if (msgEl) {
                msgEl.innerHTML = '';

                var desc = document.createElement('p');
                desc.textContent = 'Copy a helper prompt below and paste it into Claude, ChatGPT, or any AI assistant. It will guide you step-by-step to create a custom template tailored to your photography.';
                desc.style.marginBottom = '1rem';
                msgEl.appendChild(desc);

                // Vision helper button
                var visionBtn = document.createElement('button');
                visionBtn.className = 'btn btn-primary';
                visionBtn.style.cssText = 'display:block;width:100%;margin-bottom:0.5rem';
                visionBtn.textContent = 'Create Vision Prompt with AI';
                visionBtn.onclick = function() {
                    _copyText(data.vision, 'Vision helper prompt copied to clipboard');
                };
                msgEl.appendChild(visionBtn);

                // Context helper button
                var contextBtn = document.createElement('button');
                contextBtn.className = 'btn';
                contextBtn.style.cssText = 'display:block;width:100%;margin-bottom:0.5rem';
                contextBtn.textContent = 'Create Context Template with AI';
                contextBtn.onclick = function() {
                    _copyText(data.context, 'Context helper prompt copied to clipboard');
                };
                msgEl.appendChild(contextBtn);
            }
            if (okBtn) okBtn.style.display = 'none';
            document.getElementById('confirm-modal').style.display = '';
            // Restore OK button on close
            var origClose = window.confirmCancel;
            window.confirmCancel = function() {
                if (okBtn) okBtn.style.display = '';
                origClose();
                window.confirmCancel = origClose;
            };
        })
        .catch(function() { showToast('Failed to load AI helper', 'error'); });
}

// Auto-load prompts when Settings page has the prompt library section
(function() {
    if (document.getElementById('prompt-library-section')) {
        loadPrompts('vision');
        loadPrompts('context_injection');
    }
})();

// ── Bulk Download (Zip) ─────────────────────────────────────

function bulkDownload() {
    var ids = getSelectedIds();
    if (ids.length === 0) { showToast('No images selected', 'error'); return; }

    showToast('Preparing download...');
    var xhr = new XMLHttpRequest();
    xhr.open('POST', API_PREFIX + '/images/download-batch', true);
    xhr.responseType = 'blob';
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.onload = function() {
        if (xhr.status === 200) {
            var blob = xhr.response;
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = 'a-eye_download.zip';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            showToast('Download started');
        } else {
            showToast('Download failed', 'error');
        }
    };
    xhr.onerror = function() { showToast('Download failed', 'error'); };
    xhr.send(JSON.stringify({ image_ids: ids }));
}

// ── Read-Only Banner Dismissal ───────────────────────────────

function dismissReadonlyBanner() {
    var banner = document.getElementById('readonly-banner');
    if (banner) banner.style.display = 'none';
    try { localStorage.setItem('a-eye-readonly-dismissed', '1'); } catch(e) {}
}

(function() {
    var banner = document.getElementById('readonly-banner');
    if (banner) {
        try {
            if (localStorage.getItem('a-eye-readonly-dismissed') === '1') {
                banner.style.display = 'none';
            }
        } catch(e) {}
    }
})();

// ── Database Management ──────────────────────────────────────

function backupDatabase() {
    showToast('Creating backup...');
    fetch(API_PREFIX + '/database/backup', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        showToast('Backup saved: ' + data.filename);
        // Trigger download
        var a = document.createElement('a');
        a.href = API_PREFIX + '/database/backup/' + encodeURIComponent(data.filename) + '/download';
        a.download = data.filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        loadBackupList();
    })
    .catch(function() { showToast('Backup failed', 'error'); });
}

function loadBackupList() {
    var el = document.getElementById('backup-list');
    if (!el) return;
    fetch(API_PREFIX + '/database/backup/list')
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (!data.backups || data.backups.length === 0) {
            el.innerHTML = '<small class="text-muted">No backups yet</small>';
            return;
        }
        var count = data.backups.length;
        var html = '<details class="backup-list-details">';
        html += '<summary class="backup-list-summary">' + count + ' backup' + (count !== 1 ? 's' : '') + ' saved</summary>';
        html += '<table class="table" style="font-size:0.8rem;margin-top:0.5rem"><thead><tr><th>Backup</th><th>Size</th><th>Date</th><th></th></tr></thead><tbody>';
        for (var i = 0; i < data.backups.length; i++) {
            var b = data.backups[i];
            html += '<tr><td><a href="' + API_PREFIX + '/database/backup/' + encodeURIComponent(b.filename) + '/download" download>' + _escHtml(b.filename) + '</a></td>';
            html += '<td>' + _escHtml(b.size_human) + '</td>';
            html += '<td>' + _escHtml(b.created_at) + '</td>';
            html += '<td><button class="btn btn-xs btn-error" onclick="deleteBackup(\'' + _escHtml(b.filename) + '\')">Delete</button></td></tr>';
        }
        html += '</tbody></table></details>';
        el.innerHTML = html;
    })
    .catch(function() {});
}

function deleteBackup(filename) {
    showConfirm('Delete backup?', 'Delete ' + filename + '? This cannot be undone.', 'Delete', function() {
        fetch(API_PREFIX + '/database/backup/' + encodeURIComponent(filename), { method: 'DELETE' })
        .then(function(r) { return r.json(); })
        .then(function() {
            showToast('Backup deleted');
            loadBackupList();
        })
        .catch(function() { showToast('Failed to delete backup', 'error'); });
    });
}

function _escHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ── Library Verification ─────────────────────────────────────

function verifyLibrary() {
    var el = document.getElementById('verify-results');
    if (el) { el.style.display = ''; el.innerHTML = '<p class="text-muted">Verifying library...</p>'; }
    showToast('Verification started...');
    fetch(API_PREFIX + '/database/verify', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (!el) return;
        var html = '';

        // Matched
        html += '<div class="verify-section verify-matched"><strong>' + data.matched_count + ' files verified</strong> — all good</div>';

        // New on disk
        if (data.new_count > 0) {
            html += '<div class="verify-section verify-new"><strong>' + data.new_count + ' new files on disk</strong> not yet in database.';
            html += ' <a href="/">Scan your library</a> to process them.</div>';
        }

        // Missing from disk
        if (data.missing_count > 0) {
            html += '<div class="verify-section verify-missing">';
            html += '<strong>' + data.missing_count + ' orphaned records</strong> — files no longer on disk:';
            html += '<div class="verify-file-list">';
            var ids = [];
            for (var i = 0; i < data.missing_from_disk.length; i++) {
                var m = data.missing_from_disk[i];
                ids.push(m.id);
                html += '<div class="verify-file-item">' + _escHtml(m.file_path) + ' <span class="badge badge-' + (m.status === 'renamed' ? 'success' : 'warning') + '">' + _escHtml(m.status) + '</span></div>';
            }
            html += '</div>';
            html += '<button class="btn btn-sm btn-warning" style="margin-top:0.5rem" onclick="cleanupOrphans([' + ids.join(',') + '])">Remove Orphaned Records</button>';
            html += '</div>';
        }

        if (data.new_count === 0 && data.missing_count === 0) {
            html += '<div class="verify-section verify-matched">Library is in perfect sync.</div>';
        }

        el.innerHTML = html;
    })
    .catch(function() {
        showToast('Verification failed', 'error');
        if (el) el.style.display = 'none';
    });
}

function cleanupOrphans(ids) {
    showConfirm('Remove orphaned records?',
        'This will permanently delete ' + ids.length + ' database records for files that no longer exist on disk.',
        'Remove',
        function() {
            fetch(API_PREFIX + '/database/verify/cleanup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ image_ids: ids })
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                showToast(data.removed + ' orphaned records removed');
                verifyLibrary(); // Re-run to refresh results
            })
            .catch(function() { showToast('Cleanup failed', 'error'); });
        }, 'btn-warning');
}

// ── Database Restore ─────────────────────────────────────────

function showConfirmTyped(title, message, confirmWord, callback) {
    var titleEl = document.getElementById('confirm-title');
    var msgEl = document.getElementById('confirm-message');
    var okBtn = document.getElementById('confirm-ok-btn');

    if (titleEl) titleEl.textContent = title;
    if (msgEl) {
        msgEl.innerHTML = '';
        var p = document.createElement('p');
        p.textContent = message;
        msgEl.appendChild(p);

        var label = document.createElement('p');
        label.style.marginTop = '0.75rem';
        label.innerHTML = 'Type <strong>' + confirmWord + '</strong> to confirm:';
        msgEl.appendChild(label);

        var input = document.createElement('input');
        input.type = 'text';
        input.className = 'confirm-typed-input';
        input.placeholder = confirmWord;
        input.autocomplete = 'off';
        msgEl.appendChild(input);

        okBtn.disabled = true;
        okBtn.textContent = confirmWord;
        okBtn.className = 'btn btn-error';

        input.addEventListener('input', function() {
            okBtn.disabled = (input.value !== confirmWord);
        });
    }

    _confirmCallback = function() {
        okBtn.disabled = false;
        callback();
    };
    document.getElementById('confirm-modal').style.display = '';

    // Clean up on cancel
    var origCancel = window.confirmCancel;
    window.confirmCancel = function() {
        okBtn.disabled = false;
        origCancel();
        window.confirmCancel = origCancel;
    };
}

function restoreDatabase() {
    // Fetch backup list first
    fetch(API_PREFIX + '/database/backup/list')
    .then(function(r) { return r.json(); })
    .then(function(data) {
        _showRestoreModal(data.backups || []);
    })
    .catch(function() { showToast('Failed to load backups', 'error'); });
}

function _showRestoreModal(backups) {
    var titleEl = document.getElementById('confirm-title');
    var msgEl = document.getElementById('confirm-message');
    var okBtn = document.getElementById('confirm-ok-btn');

    if (titleEl) titleEl.textContent = 'Restore Database';
    if (msgEl) {
        msgEl.innerHTML = '';

        var warn = document.createElement('p');
        warn.style.cssText = 'color: var(--warning); margin-bottom: 0.75rem';
        warn.textContent = 'This will replace your entire database with the selected backup. A pre-restore backup will be created automatically.';
        msgEl.appendChild(warn);

        // Backup dropdown
        if (backups.length > 0) {
            var label1 = document.createElement('label');
            label1.textContent = 'Restore from server backup:';
            label1.style.fontWeight = '600';
            msgEl.appendChild(label1);

            var select = document.createElement('select');
            select.id = 'restore-backup-select';
            select.style.cssText = 'width:100%;margin:0.5rem 0';
            var opt0 = document.createElement('option');
            opt0.value = '';
            opt0.textContent = 'Select a backup...';
            select.appendChild(opt0);
            for (var i = 0; i < backups.length; i++) {
                var opt = document.createElement('option');
                opt.value = backups[i].filename;
                opt.textContent = backups[i].filename + ' (' + backups[i].size_human + ')';
                select.appendChild(opt);
            }
            msgEl.appendChild(select);
        }

        // Or upload
        var divider = document.createElement('p');
        divider.style.cssText = 'text-align:center;color:var(--text-muted);margin:0.5rem 0;font-size:0.85rem';
        divider.textContent = backups.length > 0 ? '— or —' : '';
        msgEl.appendChild(divider);

        var label2 = document.createElement('label');
        label2.textContent = 'Upload a backup file:';
        label2.style.fontWeight = '600';
        msgEl.appendChild(label2);

        var fileInput = document.createElement('input');
        fileInput.type = 'file';
        fileInput.id = 'restore-file-input';
        fileInput.accept = '.db';
        fileInput.style.cssText = 'width:100%;margin:0.5rem 0';
        msgEl.appendChild(fileInput);

        // Confirm word
        var confirmLabel = document.createElement('p');
        confirmLabel.style.marginTop = '0.75rem';
        confirmLabel.innerHTML = 'Type <strong>RESTORE</strong> to confirm:';
        msgEl.appendChild(confirmLabel);

        var input = document.createElement('input');
        input.type = 'text';
        input.className = 'confirm-typed-input';
        input.placeholder = 'RESTORE';
        input.autocomplete = 'off';
        input.id = 'restore-confirm-input';
        msgEl.appendChild(input);

        okBtn.disabled = true;
        okBtn.textContent = 'Restore';
        okBtn.className = 'btn btn-error';

        input.addEventListener('input', function() {
            okBtn.disabled = (input.value !== 'RESTORE');
        });
    }

    _confirmCallback = function() {
        okBtn.disabled = false;
        var selectEl = document.getElementById('restore-backup-select');
        var fileEl = document.getElementById('restore-file-input');

        if (fileEl && fileEl.files && fileEl.files.length > 0) {
            _restoreFromUpload(fileEl.files[0]);
        } else if (selectEl && selectEl.value) {
            _restoreFromBackup(selectEl.value);
        } else {
            showToast('Select a backup or upload a file', 'error');
        }
    };
    document.getElementById('confirm-modal').style.display = '';

    var origCancel = window.confirmCancel;
    window.confirmCancel = function() {
        okBtn.disabled = false;
        origCancel();
        window.confirmCancel = origCancel;
    };
}

function _restoreFromBackup(filename) {
    showToast('Restoring database...');
    fetch(API_PREFIX + '/database/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: filename })
    })
    .then(function(r) {
        if (!r.ok) return r.json().then(function(e) { throw new Error(e.detail || 'Restore failed'); });
        return r.json();
    })
    .then(function(data) {
        showToast('Database restored from ' + data.restored_from);
        setTimeout(function() { location.reload(); }, 1000);
    })
    .catch(function(err) { showToast(err.message || 'Restore failed', 'error'); });
}

function _restoreFromUpload(file) {
    showToast('Uploading and restoring...');
    var formData = new FormData();
    formData.append('file', file);
    fetch(API_PREFIX + '/database/restore/upload', {
        method: 'POST',
        body: formData
    })
    .then(function(r) {
        if (!r.ok) return r.json().then(function(e) { throw new Error(e.detail || 'Restore failed'); });
        return r.json();
    })
    .then(function(data) {
        showToast('Database restored from uploaded file');
        setTimeout(function() { location.reload(); }, 1000);
    })
    .catch(function(err) { showToast(err.message || 'Restore failed', 'error'); });
}

// Auto-load backup list on Settings page
(function() {
    if (document.getElementById('backup-list')) {
        loadBackupList();
    }
})();

// ── Upload to Library ───────────────────────────────────────

var _uploadExts = ['.jpg','.jpeg','.png','.gif','.bmp','.tiff','.tif','.webp','.heic','.heif','.avif','.cr2','.nef','.arw','.dng','.orf','.rw2','.pef','.srw','.raf'];

function _filterImageFiles(files) {
    var supported = [];
    for (var i = 0; i < files.length; i++) {
        var name = files[i].name.toLowerCase();
        var dot = name.lastIndexOf('.');
        if (dot !== -1 && _uploadExts.indexOf(name.substring(dot)) !== -1) {
            supported.push(files[i]);
        }
    }
    return supported;
}

function handleLibraryUpload(files, subfolder) {
    var supported = _filterImageFiles(files);
    if (supported.length === 0) { showToast('No supported image files selected', 'error'); return; }

    var formData = new FormData();
    supported.forEach(function(f) { formData.append('files', f); });
    if (subfolder) formData.append('subfolder', subfolder);

    var progressBar = document.getElementById('upload-progress-bar');
    var progressContainer = document.getElementById('upload-progress');
    if (progressContainer) progressContainer.style.display = '';
    if (progressBar) progressBar.style.width = '0%';

    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload', true);
    xhr.upload.onprogress = function(e) {
        if (e.lengthComputable && progressBar) {
            progressBar.style.width = Math.round((e.loaded / e.total) * 100) + '%';
        }
    };
    xhr.onload = function() {
        if (progressContainer) progressContainer.style.display = 'none';
        if (xhr.status === 200) {
            var data = JSON.parse(xhr.responseText);
            showToast('Uploaded ' + (data.saved || 0) + ' files');
        } else {
            showToast('Upload failed', 'error');
        }
    };
    xhr.onerror = function() {
        if (progressContainer) progressContainer.style.display = 'none';
        showToast('Upload failed', 'error');
    };
    xhr.send(formData);
}

function initUploadDropZone(zoneId, fileInputId, onFiles) {
    var zone = document.getElementById(zoneId);
    var input = document.getElementById(fileInputId);
    if (!zone) return;

    zone.addEventListener('dragover', function(e) {
        e.preventDefault();
        zone.classList.add('drag-over');
    });
    zone.addEventListener('dragleave', function(e) {
        e.preventDefault();
        zone.classList.remove('drag-over');
    });
    zone.addEventListener('drop', function(e) {
        e.preventDefault();
        zone.classList.remove('drag-over');
        if (e.dataTransfer.files.length > 0) onFiles(e.dataTransfer.files);
    });
    if (input) {
        input.addEventListener('change', function() {
            if (input.files.length > 0) onFiles(input.files);
            input.value = '';
        });
    }
}

var _uploadTreeLoaded = false;

function toggleUploadTree() {
    var panel = document.getElementById('upload-tree-panel');
    var btn = document.getElementById('upload-tree-btn');
    if (!panel || !btn) return;
    var isHidden = panel.style.display === 'none';
    if (isHidden) {
        panel.style.display = '';
        btn.textContent = 'Hide';
        if (!_uploadTreeLoaded) {
            loadUploadTree();
            _uploadTreeLoaded = true;
        }
    } else {
        panel.style.display = 'none';
        btn.textContent = 'Change Folder';
    }
}

function loadUploadTree(autoSelect) {
    var container = document.getElementById('upload-tree-container');
    if (!container) return;
    container.innerHTML = '<div class="folder-tree-loading">Loading...</div>';

    fetch('/api/folders')
    .then(function(r) { return r.json(); })
    .then(function(data) {
        var tree = data.tree || [];
        container.innerHTML = '';
        var treeEl = document.createElement('div');
        treeEl.className = 'folder-tree';

        var currentPath = autoSelect !== undefined ? autoSelect :
            (document.getElementById('upload-subfolder') || {}).value || '';

        // Add root entry
        var rootUl = document.createElement('ul');
        var rootLi = document.createElement('li');
        var rootRow = document.createElement('div');
        rootRow.className = 'folder-node selectable' + (currentPath === '' ? ' selected' : '');
        rootRow.dataset.path = '';
        var rootSpacer = document.createElement('button');
        rootSpacer.className = 'folder-toggle leaf';
        rootSpacer.type = 'button';
        rootSpacer.innerHTML = '&#9660;';
        var rootName = document.createElement('span');
        rootName.className = 'folder-name selectable';
        rootName.textContent = '/ (root)';
        rootRow.appendChild(rootSpacer);
        rootRow.appendChild(rootName);
        rootRow.addEventListener('click', function() { selectUploadFolder('', '/ (root)'); });
        rootLi.appendChild(rootRow);

        // Render children
        var childrenNodes = [];
        tree.forEach(function(node) {
            if (node.path === '.' || node.path === '__root_files__') {
                if (node.children) childrenNodes = childrenNodes.concat(node.children);
            } else {
                childrenNodes.push(node);
            }
        });
        if (childrenNodes.length > 0) {
            var childUl = renderUploadTreeNodes(childrenNodes, currentPath);
            rootLi.appendChild(childUl);
        }
        rootUl.appendChild(rootLi);
        treeEl.appendChild(rootUl);
        container.appendChild(treeEl);
    })
    .catch(function() {
        container.innerHTML = '<p class="text-muted">Could not load folder tree.</p>';
    });
}

function renderUploadTreeNodes(nodes, selectedPath) {
    var ul = document.createElement('ul');
    nodes.forEach(function(node) {
        if (node.path === '__root_files__') return;
        var li = document.createElement('li');

        var row = document.createElement('div');
        row.className = 'folder-node selectable';
        if (node.path === selectedPath) row.classList.add('selected');
        row.dataset.path = node.path;

        var toggle = document.createElement('button');
        toggle.className = 'folder-toggle' + (node.children.length === 0 ? ' leaf' : '');
        toggle.type = 'button';
        toggle.innerHTML = '&#9660;';
        toggle.addEventListener('click', function(e) {
            e.stopPropagation();
            var childUl = li.querySelector(':scope > ul');
            if (!childUl) return;
            var isCollapsed = childUl.style.display === 'none';
            childUl.style.display = isCollapsed ? '' : 'none';
            toggle.classList.toggle('collapsed', !isCollapsed);
        });

        var nameSpan = document.createElement('span');
        nameSpan.className = 'folder-name selectable';
        nameSpan.textContent = node.name;
        nameSpan.title = node.path;

        row.appendChild(toggle);
        row.appendChild(nameSpan);
        row.addEventListener('click', function() {
            selectUploadFolder(node.path, node.name);
        });
        li.appendChild(row);

        if (node.children.length > 0) {
            var childUl = renderUploadTreeNodes(node.children, selectedPath);
            li.appendChild(childUl);
        }
        ul.appendChild(li);
    });
    return ul;
}

function selectUploadFolder(path, displayName) {
    var input = document.getElementById('upload-subfolder');
    if (input) input.value = path;
    var label = document.getElementById('upload-folder-label');
    if (label) label.textContent = displayName || '/ (root)';

    // Update selected highlight
    var container = document.getElementById('upload-tree-container');
    if (container) {
        container.querySelectorAll('.folder-node.selected').forEach(function(el) {
            el.classList.remove('selected');
        });
        var match = container.querySelector('.folder-node[data-path="' + CSS.escape(path) + '"]');
        if (match) match.classList.add('selected');
    }

    // Collapse tree
    var panel = document.getElementById('upload-tree-panel');
    var btn = document.getElementById('upload-tree-btn');
    if (panel) panel.style.display = 'none';
    if (btn) btn.textContent = 'Change Folder';
}

function toggleNewFolder() {
    var form = document.getElementById('new-folder-form');
    if (!form) return;
    var visible = form.style.display !== 'none';
    form.style.display = visible ? 'none' : '';
    if (!visible) {
        var input = document.getElementById('upload-subfolder');
        var parent = input ? input.value : '';
        var label = document.getElementById('new-folder-parent');
        if (label) label.textContent = (parent && parent !== '.') ? parent + '/' : '/';
        var nameInput = document.getElementById('new-folder-name');
        if (nameInput) { nameInput.value = ''; nameInput.focus(); }
    }
}

function createNewFolder() {
    var nameInput = document.getElementById('new-folder-name');
    var name = nameInput ? nameInput.value.trim() : '';
    if (!name) { showToast('Enter a folder name', 'error'); return; }

    var input = document.getElementById('upload-subfolder');
    var parent = input ? input.value : '';
    if (parent === '.') parent = '';

    fetch('/api/folders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parent: parent, name: name })
    })
    .then(function(r) {
        if (!r.ok) return r.json().then(function(d) { throw new Error(d.detail || 'Failed'); });
        return r.json();
    })
    .then(function(data) {
        showToast('Folder created: ' + data.path);
        selectUploadFolder(data.path, data.path.split('/').pop());
        _uploadTreeLoaded = false;
        toggleNewFolder();
    })
    .catch(function(err) { showToast(err.message || 'Failed to create folder', 'error'); });
}

// ── Workspace ───────────────────────────────────────────────

var _wsPolling = null;

function handleWorkspaceUpload(files) {
    var supported = _filterImageFiles(files);
    if (supported.length === 0) { showToast('No supported image files selected', 'error'); return; }

    var formData = new FormData();
    supported.forEach(function(f) { formData.append('files', f); });

    var progressBar = document.getElementById('ws-upload-progress-bar');
    var progressContainer = document.getElementById('ws-upload-progress');
    if (progressContainer) progressContainer.style.display = '';
    if (progressBar) progressBar.style.width = '0%';

    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/workspace/upload', true);
    xhr.upload.onprogress = function(e) {
        if (e.lengthComputable && progressBar) {
            progressBar.style.width = Math.round((e.loaded / e.total) * 100) + '%';
        }
    };
    xhr.onload = function() {
        if (progressContainer) progressContainer.style.display = 'none';
        if (xhr.status === 200) {
            var data = JSON.parse(xhr.responseText);
            showToast('Uploaded ' + (data.saved || 0) + ' files to workspace');
            pollWorkspaceStats();
        } else {
            showToast('Upload failed', 'error');
        }
    };
    xhr.onerror = function() {
        if (progressContainer) progressContainer.style.display = 'none';
        showToast('Upload failed', 'error');
    };
    xhr.send(formData);
}

function pollWorkspaceStats() {
    fetch('/api/workspace/stats')
    .then(function(r) { return r.json(); })
    .then(function(data) {
        var card = document.getElementById('workspace-card');
        if (!card) return;

        var statusEl = document.getElementById('ws-status');
        var statsEl = document.getElementById('ws-stats');
        var actionsEl = document.getElementById('ws-actions');
        var emptyEl = document.getElementById('ws-empty');
        var activeEl = document.getElementById('ws-active');

        var total = data.total || 0;
        var pending = data.pending || 0;
        var processing = data.processing || 0;
        var proposed = data.proposed || 0;
        var renamed = data.renamed || 0;
        var skipped = data.skipped || 0;
        var isProcessing = data.processing_active || false;

        if (total === 0) {
            if (emptyEl) emptyEl.style.display = '';
            if (activeEl) activeEl.style.display = 'none';
        } else {
            if (emptyEl) emptyEl.style.display = 'none';
            if (activeEl) activeEl.style.display = '';

            if (statusEl) {
                if (isProcessing || processing > 0) {
                    statusEl.innerHTML = '<span class="badge badge-processing">Processing ' + (pending + processing) + '...</span>';
                } else if (proposed > 0) {
                    statusEl.innerHTML = '<span class="badge badge-proposed">' + proposed + ' ready for review</span>';
                } else if (renamed > 0) {
                    statusEl.innerHTML = '<span class="badge badge-renamed">' + renamed + ' ready to download</span>';
                } else {
                    statusEl.innerHTML = '<span class="badge">' + total + ' files</span>';
                }
            }

            if (statsEl) {
                var parts = [];
                if (pending > 0) parts.push(pending + ' pending');
                if (processing > 0) parts.push(processing + ' processing');
                if (proposed > 0) parts.push(proposed + ' proposed');
                if (renamed > 0) parts.push(renamed + ' renamed');
                if (skipped > 0) parts.push(skipped + ' skipped');
                statsEl.textContent = parts.join(', ') || total + ' total';
            }

            // Update collapsible summary badge
            var badge = document.getElementById('ws-summary-badge');
            if (badge) {
                var badgeParts = [];
                if (proposed > 0) badgeParts.push(proposed + ' proposed');
                if (renamed > 0) badgeParts.push(renamed + ' renamed');
                if (processing > 0) badgeParts.push(processing + ' processing');
                badge.textContent = badgeParts.length ? badgeParts.join(', ') : total + ' files';
            }

            if (actionsEl) {
                var html = '';
                if (proposed > 0) {
                    html += '<a href="/review?source=workspace" class="btn btn-sm btn-success">Review Workspace</a> ';
                }
                if (renamed > 0) {
                    html += '<button class="btn btn-sm btn-primary" onclick="downloadWorkspace()">Download Renamed (' + renamed + ')</button> ';
                }
                html += '<a href="/queue?source=workspace" class="btn btn-sm">View All</a> ';
                html += '<button class="btn btn-sm btn-error" onclick="clearWorkspace()">Clear</button>';
                actionsEl.innerHTML = html;
            }
        }

        // Continue polling while processing
        if (isProcessing || processing > 0 || pending > 0) {
            if (!_wsPolling) {
                _wsPolling = setInterval(pollWorkspaceStats, 3000);
            }
        } else {
            if (_wsPolling) {
                clearInterval(_wsPolling);
                _wsPolling = null;
            }
        }
    })
    .catch(function() {});
}

function downloadWorkspace() {
    showToast('Preparing workspace download...');
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/workspace/download', true);
    xhr.responseType = 'blob';
    xhr.onload = function() {
        if (xhr.status === 200) {
            var blob = xhr.response;
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = 'workspace_renamed.zip';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            showToast('Download complete. You can now clear the workspace.');
        } else {
            showToast('Download failed', 'error');
        }
    };
    xhr.onerror = function() { showToast('Download failed', 'error'); };
    xhr.send();
}

function clearWorkspace() {
    showConfirm('Clear workspace?',
        'This will permanently delete all files in the workspace. Download any renamed files first.',
        'Clear Workspace',
        function() {
            fetch('/api/workspace/clear', { method: 'DELETE' })
            .then(function(r) { return r.json(); })
            .then(function() {
                showToast('Workspace cleared');
                pollWorkspaceStats();
            })
            .catch(function() { showToast('Failed to clear workspace', 'error'); });
        });
}

// ── Dashboard Splash Screen ─────────────────────────────────

(function() {
    var overlay = document.getElementById('splash-overlay');
    if (!overlay) return;
    if (sessionStorage.getItem('splash_shown')) {
        overlay.style.display = 'none';
        return;
    }

    var quoteEl = document.getElementById('splash-quote');
    if (quoteEl) quoteEl.textContent = _randomQuote();

    sessionStorage.setItem('splash_shown', '1');
    setTimeout(function() {
        overlay.classList.remove('visible');
        setTimeout(function() { overlay.style.display = 'none'; }, 800);
    }, 6000);
})();
