/* The dashboard's only script, and progressive enhancement only.
 *
 * Every page is complete without it: each action is a plain form, and a
 * deployment in progress reloads itself when scripting is off. With it:
 *
 *   - the build log grows as the build runs (server-sent events);
 *   - the deployment's header and line follow the engine's state in place,
 *     from `state` events on the same connection, so the log is never
 *     interrupted by a reload (docs/design, Phase 5 §3.3);
 *   - elapsed times count up, so nothing in progress is a number frozen at
 *     the moment the page loaded.
 *
 * It never guesses: a state is shown only once the engine has recorded it.
 */
(function () {
  'use strict';

  function duration(seconds) {
    seconds = Math.max(0, Math.round(seconds));
    if (seconds < 60) return seconds + 's';
    var minutes = Math.floor(seconds / 60);
    seconds = seconds % 60;
    if (minutes < 60) return minutes + 'm ' + (seconds < 10 ? '0' : '') + seconds + 's';
    var hours = Math.floor(minutes / 60);
    minutes = minutes % 60;
    return hours + 'h ' + (minutes < 10 ? '0' : '') + minutes + 'm';
  }

  function since(iso) {
    return (Date.now() - new Date(iso).getTime()) / 1000;
  }

  // Elapsed times: every element that says when it started counts up.
  function tick() {
    var counters = document.querySelectorAll('[data-since]');
    for (var i = 0; i < counters.length; i++) {
      counters[i].textContent = duration(since(counters[i].getAttribute('data-since')));
    }
  }
  if (document.querySelector('[data-since]')) {
    setInterval(tick, 1000);
  }

  var log = document.getElementById('log');
  if (!log || log.dataset.terminal === 'true') return;

  var logStatus = document.getElementById('log-status');
  var shortId = log.dataset.shortId;
  var cursor = parseInt(log.dataset.cursor || '0', 10);
  var placeholder = log.querySelector('.log-empty');

  // Only auto-scroll while the reader is already at the bottom. Yanking the
  // view back down while someone is reading an error further up is the most
  // irritating thing a live log can do.
  function atBottom() {
    return log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  }

  function append(entry) {
    if (placeholder) { placeholder.remove(); placeholder = null; }
    var stick = atBottom();
    var line = document.createElement('span');
    line.className = 'log-line log-' + entry.stream;
    line.textContent = entry.line;
    log.appendChild(line);
    if (stick) log.scrollTop = log.scrollHeight;
  }

  // --- the header and the deployment line, redrawn from a state event -----

  var NODE_STATES = ['complete', 'active', 'failed', 'pending', 'current',
                     'not-current', 'cancelled', 'end'];
  var LABELS = { source: 'Source', build: 'Build', deploy: 'Deploy' };

  function fromTemplate(id) {
    var template = document.getElementById(id);
    return template ? template.content.cloneNode(true) : null;
  }

  function setNode(key, state, detail) {
    var node = document.querySelector('#dline .node-' + key);
    if (!node) return;
    for (var i = 0; i < NODE_STATES.length; i++) node.classList.remove('node-' + NODE_STATES[i]);
    node.classList.add('node-' + state);
    if (state === 'active') node.setAttribute('aria-current', 'step');
    else node.removeAttribute('aria-current');

    var markId = state === 'complete' ? 'tpl-mark-complete'
      : key === 'build' ? 'tpl-mark-building' : 'tpl-mark-deploying';
    var mark = fromTemplate(markId);
    var holder = node.querySelector('.node-mark');
    if (mark && holder && (state === 'complete' || state === 'active')) {
      holder.textContent = '';
      holder.appendChild(mark);
    }
    var shown = node.querySelector('.node-detail');
    if (shown) shown.textContent = state === 'active' ? 'in progress' : detail;
    var spoken = node.querySelector('.visually-hidden');
    if (spoken) {
      spoken.textContent = LABELS[key] + ': ' + (state === 'active' ? 'in progress'
        : 'completed' + (detail ? ' in ' + detail : ''));
    }
  }

  function between(start, end) {
    return start && end ? duration((new Date(end) - new Date(start)) / 1000) : '';
  }

  function applyState(state) {
    var status = state.status;
    log.dataset.status = status;
    if (status !== 'building' && status !== 'deploying') {
      // Ready, failed or cancelled: the stream sends `done` once the last log
      // lines are in, and the page reloads with the actions that state allows.
      if (logStatus) logStatus.textContent = 'finishing…';
      return;
    }

    var header = document.getElementById('dstatus');
    var replacement = fromTemplate('tpl-status-' + status);
    if (header && replacement) {
      header.textContent = '';
      header.appendChild(replacement);
    }

    // Source shows when the deployment was created, as the server draws it.
    setNode('source', 'complete', state.created_at
      ? new Date(state.created_at).toISOString().slice(11, 19) : '');
    if (status === 'building') {
      setNode('build', 'active', '');
    } else {
      setNode('build', 'complete', between(state.started_at, state.built_at));
      setNode('deploy', 'active', '');
    }

    var elapsed = document.getElementById('delapsed');
    var start = status === 'building' ? state.started_at : state.built_at;
    if (elapsed && start) elapsed.setAttribute('data-since', start);

    var sentence = document.getElementById('dsentence');
    if (sentence) {
      sentence.textContent = status === 'deploying'
        ? 'Starting the container and checking it answers.' : '';
    }
    var asOf = document.getElementById('dasof');
    if (asOf) asOf.textContent = 'live';
  }

  // --- the stream -----------------------------------------------------------

  var url = '/api/deployments/' + shortId + '/logs/stream?after=' + cursor +
            '&seen=' + encodeURIComponent(log.dataset.status || '');
  var source = new EventSource(url);

  source.onmessage = function (event) {
    var entry = JSON.parse(event.data);
    cursor = entry.seq;
    append(entry);
  };

  source.addEventListener('state', function (event) {
    applyState(JSON.parse(event.data));
  });

  source.addEventListener('done', function (event) {
    source.close();
    var result = JSON.parse(event.data);
    if (logStatus) logStatus.textContent = 'finished — ' + result.status;
    // Reload so the page's actions match the new state: a finished deploy
    // offers what its state allows, which the streaming page did not.
    setTimeout(function () { window.location.reload(); }, 900);
  });

  source.onopen = function () {
    if (logStatus) logStatus.textContent = 'live';
  };

  source.onerror = function () {
    // EventSource reconnects by itself; say so rather than looking frozen.
    if (logStatus) logStatus.textContent = 'reconnecting…';
  };

  log.scrollTop = log.scrollHeight;
})();
