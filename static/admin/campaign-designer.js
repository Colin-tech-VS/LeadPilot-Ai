/* Campaign designer — block editor for /admin/campagnes/<id>.
 *
 * The canvas is an approximation of the e-mail, tuned for editing: blocks are
 * cards you can select, drag and type into. The truth is the server renderer,
 * which the "Aperçu réel" tab shows in an iframe — so the canvas never has to
 * pretend to be a mail client, and the two can never disagree about what gets
 * sent, because only one of them produces the HTML that ships.
 */
(function () {
  'use strict';

  var root = document.querySelector('.camp-editor');
  if (!root) return;

  var EDITABLE = root.dataset.editable === '1';
  var AI = root.dataset.ai === '1';
  var initial = JSON.parse(document.getElementById('camp-initial').textContent || '{}');

  var state = {
    design: normaliseDesign(initial.design),
    segment: initial.segment && Object.keys(initial.segment).length ? initial.segment : {
      trades: [], cities: [], statuses: ['new', 'ready'], sources: [],
      exclude_contacted: true, with_listing: false, limit: 200
    },
    selected: null,
    sending: false,
    lastFocusedField: null
  };

  function normaliseDesign(design) {
    var d = design && typeof design === 'object' ? design : {};
    return {
      settings: Object.assign(
        { bg: '#EFE9DC', surface: '#FBF7EE', ink: '#1C1914', muted: '#6B6458',
          accent: '#121820', border: '#C4B79A', width: 600 },
        d.settings || {}
      ),
      blocks: Array.isArray(d.blocks) ? d.blocks.slice() : []
    };
  }

  // ── helpers ──────────────────────────────────────────────────────────────
  function uid() { return Math.random().toString(36).slice(2, 12); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function stripTags(html) {
    var tmp = document.createElement('div');
    tmp.innerHTML = html || '';
    return tmp.textContent || '';
  }
  function blockIndex(id) {
    for (var i = 0; i < state.design.blocks.length; i++) {
      if (state.design.blocks[i].id === id) return i;
    }
    return -1;
  }
  function selectedBlock() {
    var i = blockIndex(state.selected);
    return i === -1 ? null : state.design.blocks[i];
  }

  // ── defaults for each block type ─────────────────────────────────────────
  var DEFAULTS = {
    header:  function () { return { type: 'header', title: 'PilotCore', tagline: '', logo: true, align: 'left' }; },
    heading: function () { return { type: 'heading', text: 'Votre titre', align: 'left', size: 26 }; },
    text:    function () { return { type: 'text', html: '<p>{{salutation}}, écrivez votre message ici.</p>', size: 16, align: 'left' }; },
    button:  function () { return { type: 'button', label: 'Essayer gratuitement', url: '{{lien_inscription}}', align: 'left' }; },
    image:   function () { return { type: 'image', src: '', alt: '', width: 520, align: 'center', href: '' }; },
    list:    function () { return { type: 'list', items: ['Premier argument', 'Deuxième argument'], icon: '✓' }; },
    offer:   function () { return { type: 'offer', name: 'Pro', price: '349 €', period: 'HT / mois',
                                    description: '', features: ['Première inclusion'], badge: '',
                                    highlight: false, cta_label: 'Voir l’offre', cta_url: '{{lien_inscription}}' }; },
    stats:   function () { return { type: 'stats', items: [
                                    { value: '24h/24', label: 'Appels pris' },
                                    { value: '14 j', label: 'Essai gratuit' },
                                    { value: '0 €', label: 'Sans engagement' }] }; },
    quote:   function () { return { type: 'quote', text: 'Ce que dit un client.', author: 'Prénom, métier' }; },
    divider: function () { return { type: 'divider' }; },
    spacer:  function () { return { type: 'spacer', height: 24 }; },
    footer:  function () { return { type: 'footer', html: '<p>Une question ? Répondez simplement à cet e-mail.</p>', align: 'left' }; }
  };

  var LABELS = {
    header: 'En-tête', heading: 'Titre', text: 'Texte', button: 'Bouton', image: 'Image',
    list: 'Liste', offer: 'Offre', stats: 'Chiffres', quote: 'Citation',
    divider: 'Séparateur', spacer: 'Espace', footer: 'Pied de page'
  };

  // ── persistence ──────────────────────────────────────────────────────────
  var saveTimer = null;
  var saveState = document.getElementById('camp-save-state');

  function markDirty() {
    if (!EDITABLE) return;
    if (saveState) { saveState.textContent = 'Modifications non enregistrées'; saveState.className = 'camp-save camp-save--dirty'; }
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 900);
  }

  function save() {
    if (!EDITABLE) return;
    var payload = {
      name: document.getElementById('camp-name').value,
      subject: document.getElementById('camp-subject').value,
      preheader: document.getElementById('camp-preheader').value,
      reply_to: document.getElementById('camp-replyto').value,
      design: state.design,
      segment: readSegment()
    };
    var brief = document.getElementById('camp-ai-brief');
    if (brief) payload.ai_prompt = brief.value;

    if (saveState) { saveState.textContent = 'Enregistrement…'; saveState.className = 'camp-save'; }
    fetch(root.dataset.saveUrl, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!saveState) return;
        if (!res.ok) { saveState.textContent = res.data.error || 'Échec de l’enregistrement'; saveState.className = 'camp-save camp-save--error'; return; }
        saveState.textContent = 'Enregistré'; saveState.className = 'camp-save camp-save--ok';
      })
      .catch(function () {
        if (saveState) { saveState.textContent = 'Hors ligne — non enregistré'; saveState.className = 'camp-save camp-save--error'; }
      });
  }

  // ── canvas ───────────────────────────────────────────────────────────────
  var canvas = document.getElementById('camp-canvas');

  function applyCanvasTheme() {
    var s = state.design.settings;
    canvas.style.setProperty('--camp-bg', s.bg);
    canvas.style.setProperty('--camp-surface', s.surface);
    canvas.style.setProperty('--camp-ink', s.ink);
    canvas.style.setProperty('--camp-muted', s.muted);
    canvas.style.setProperty('--camp-accent', s.accent);
    canvas.style.setProperty('--camp-width', s.width + 'px');
  }

  function renderCanvas() {
    applyCanvasTheme();
    canvas.innerHTML = '';
    var sheet = el('div', 'camp-sheet');

    if (!state.design.blocks.length) {
      sheet.appendChild(el('p', 'camp-sheet-empty', 'Ajoutez un bloc depuis la palette de gauche.'));
    }

    state.design.blocks.forEach(function (block, index) {
      var card = el('div', 'camp-block' + (block.id === state.selected ? ' selected' : ''));
      card.dataset.id = block.id;
      card.draggable = EDITABLE;

      var bar = el('div', 'camp-block-bar');
      bar.appendChild(el('span', 'camp-block-kind', LABELS[block.type] || block.type));
      var tools = el('div', 'camp-block-tools');
      [['↑', 'up', 'Monter'], ['↓', 'down', 'Descendre'],
       ['⧉', 'dup', 'Dupliquer'], ['✕', 'del', 'Supprimer']].forEach(function (t) {
        var b = el('button', 'camp-block-tool', t[0]);
        b.type = 'button'; b.dataset.act = t[1]; b.title = t[2];
        if (!EDITABLE) b.disabled = true;
        tools.appendChild(b);
      });
      bar.appendChild(tools);
      card.appendChild(bar);
      card.appendChild(renderBlockBody(block));

      card.addEventListener('click', function (ev) {
        var act = ev.target && ev.target.dataset ? ev.target.dataset.act : null;
        if (act) { ev.stopPropagation(); blockAction(block.id, act, index); return; }
        state.selected = block.id;
        renderCanvas();
        renderInspector();
      });

      if (EDITABLE) attachDrag(card);
      sheet.appendChild(card);
    });

    var legal = el('div', 'camp-block camp-block--locked');
    legal.appendChild(el('span', 'camp-block-kind', 'Mentions & désinscription'));
    legal.appendChild(el('p', 'camp-locked-note',
      'Ajouté automatiquement à chaque envoi : identité de l’expéditeur et lien de désinscription. Non supprimable.'));
    sheet.appendChild(legal);

    canvas.appendChild(sheet);
  }

  function renderBlockBody(block) {
    var body = el('div', 'camp-block-body camp-b-' + block.type);
    switch (block.type) {
      case 'header':
        body.innerHTML = '<div class="camp-b-header" style="text-align:' + (block.align || 'left') + '">' +
          (block.logo ? '<span class="camp-b-logo"></span>' : '') +
          '<span class="camp-b-brand">' + esc(block.title || 'PilotCore') + '</span>' +
          (block.tagline ? '<span class="camp-b-tagline">' + esc(block.tagline) + '</span>' : '') + '</div>';
        break;
      case 'heading':
        body.innerHTML = '<h2 style="text-align:' + (block.align || 'left') + ';font-size:' +
          (block.size || 26) + 'px">' + esc(block.text || '') + '</h2>';
        break;
      case 'text':
        body.innerHTML = '<div class="camp-b-rich" style="text-align:' + (block.align || 'left') + '">' +
          (block.html || '') + '</div>';
        break;
      case 'button':
        body.innerHTML = '<div style="text-align:' + (block.align || 'left') + '">' +
          '<span class="camp-b-btn">' + esc(block.label || 'Bouton') + '</span></div>';
        break;
      case 'image':
        body.innerHTML = block.src
          ? '<div style="text-align:' + (block.align || 'center') + '"><img src="' + esc(block.src) +
            '" alt="' + esc(block.alt || '') + '" style="max-width:100%"></div>'
          : '<div class="camp-b-placeholder">Aucune image — collez une URL dans le panneau de droite.</div>';
        break;
      case 'list':
        body.innerHTML = '<ul class="camp-b-list">' + (block.items || []).map(function (i) {
          return '<li><span>' + esc(block.icon || '✓') + '</span>' + esc(i) + '</li>';
        }).join('') + '</ul>';
        break;
      case 'offer':
        body.innerHTML = '<div class="camp-b-offer' + (block.highlight ? ' hl' : '') + '">' +
          '<p class="camp-b-offer-name">' + esc(block.name || '') +
          (block.badge ? '<span class="camp-b-badge">' + esc(block.badge) + '</span>' : '') + '</p>' +
          (block.price ? '<p class="camp-b-offer-price">' + esc(block.price) +
            '<em>' + esc(block.period || '') + '</em></p>' : '') +
          (block.description ? '<p class="camp-b-offer-desc">' + esc(block.description) + '</p>' : '') +
          '<ul>' + (block.features || []).map(function (f) { return '<li>✓ ' + esc(f) + '</li>'; }).join('') + '</ul>' +
          (block.cta_label ? '<span class="camp-b-btn small">' + esc(block.cta_label) + '</span>' : '') + '</div>';
        break;
      case 'stats':
        body.innerHTML = '<div class="camp-b-stats">' + (block.items || []).map(function (i) {
          return '<div><strong>' + esc(i.value || '') + '</strong><span>' + esc(i.label || '') + '</span></div>';
        }).join('') + '</div>';
        break;
      case 'quote':
        body.innerHTML = '<blockquote class="camp-b-quote">« ' + esc(block.text || '') + ' »' +
          (block.author ? '<cite>— ' + esc(block.author) + '</cite>' : '') + '</blockquote>';
        break;
      case 'divider':
        body.innerHTML = '<hr class="camp-b-divider">';
        break;
      case 'spacer':
        body.innerHTML = '<div class="camp-b-spacer" style="height:' + (block.height || 24) + 'px"></div>';
        break;
      case 'footer':
        body.innerHTML = '<div class="camp-b-footer">' + (block.html || '') + '</div>';
        break;
    }
    return body;
  }

  function blockAction(id, act, index) {
    if (!EDITABLE) return;
    var i = blockIndex(id);
    if (i === -1) return;
    var blocks = state.design.blocks;
    if (act === 'up' && i > 0) { blocks.splice(i - 1, 0, blocks.splice(i, 1)[0]); }
    else if (act === 'down' && i < blocks.length - 1) { blocks.splice(i + 1, 0, blocks.splice(i, 1)[0]); }
    else if (act === 'dup') {
      var copy = JSON.parse(JSON.stringify(blocks[i]));
      copy.id = uid();
      blocks.splice(i + 1, 0, copy);
      state.selected = copy.id;
    } else if (act === 'del') {
      blocks.splice(i, 1);
      if (state.selected === id) state.selected = null;
    }
    renderCanvas(); renderInspector(); markDirty();
  }

  // ── drag & drop reorder ──────────────────────────────────────────────────
  var dragId = null;
  function attachDrag(card) {
    card.addEventListener('dragstart', function (ev) {
      dragId = card.dataset.id;
      card.classList.add('dragging');
      ev.dataTransfer.effectAllowed = 'move';
      try { ev.dataTransfer.setData('text/plain', dragId); } catch (e) { /* IE guard */ }
    });
    card.addEventListener('dragend', function () {
      card.classList.remove('dragging');
      dragId = null;
      Array.prototype.forEach.call(canvas.querySelectorAll('.drop-before,.drop-after'), function (n) {
        n.classList.remove('drop-before', 'drop-after');
      });
    });
    card.addEventListener('dragover', function (ev) {
      if (!dragId || dragId === card.dataset.id) return;
      ev.preventDefault();
      var rect = card.getBoundingClientRect();
      var after = (ev.clientY - rect.top) > rect.height / 2;
      card.classList.toggle('drop-after', after);
      card.classList.toggle('drop-before', !after);
    });
    card.addEventListener('dragleave', function () {
      card.classList.remove('drop-before', 'drop-after');
    });
    card.addEventListener('drop', function (ev) {
      ev.preventDefault();
      if (!dragId || dragId === card.dataset.id) return;
      var from = blockIndex(dragId);
      var to = blockIndex(card.dataset.id);
      if (from === -1 || to === -1) return;
      var rect = card.getBoundingClientRect();
      var after = (ev.clientY - rect.top) > rect.height / 2;
      var moved = state.design.blocks.splice(from, 1)[0];
      if (from < to) to -= 1;
      state.design.blocks.splice(after ? to + 1 : to, 0, moved);
      renderCanvas(); markDirty();
    });
  }

  // ── inspector ────────────────────────────────────────────────────────────
  var inspector = document.getElementById('camp-inspector');

  function field(labelText, node) {
    var wrap = el('label', 'field');
    wrap.appendChild(el('span', null, labelText));
    wrap.appendChild(node);
    return wrap;
  }

  function input(value, onInput, type) {
    var n = document.createElement(type === 'textarea' ? 'textarea' : 'input');
    if (type && type !== 'textarea') n.type = type;
    if (type === 'textarea') n.rows = 5;
    n.className = 'admin-input';
    n.value = value == null ? '' : value;
    n.disabled = !EDITABLE;
    n.addEventListener('focus', function () { state.lastFocusedField = n; });
    n.addEventListener('input', function () { onInput(n.value); markDirty(); });
    return n;
  }

  function select(value, options, onChange) {
    var n = el('select', 'admin-select');
    n.disabled = !EDITABLE;
    options.forEach(function (o) {
      var opt = el('option', null, o[1]);
      opt.value = o[0];
      if (o[0] === value) opt.selected = true;
      n.appendChild(opt);
    });
    n.addEventListener('change', function () { onChange(n.value); markDirty(); });
    return n;
  }

  function checkbox(labelText, checked, onChange) {
    var wrap = el('label', 'camp-check');
    var n = document.createElement('input');
    n.type = 'checkbox'; n.checked = !!checked; n.disabled = !EDITABLE;
    n.addEventListener('change', function () { onChange(n.checked); markDirty(); });
    wrap.appendChild(n);
    wrap.appendChild(document.createTextNode(' ' + labelText));
    return wrap;
  }

  function linesField(labelText, items, onChange) {
    var n = input((items || []).join('\n'), function (v) {
      onChange(v.split('\n').map(function (s) { return s.trim(); }).filter(Boolean));
    }, 'textarea');
    return field(labelText, n);
  }

  var ALIGNS = [['left', 'Gauche'], ['center', 'Centré'], ['right', 'Droite']];

  function renderInspector() {
    inspector.innerHTML = '';
    var block = selectedBlock();
    if (!block) {
      inspector.appendChild(el('p', 'camp-pane-hint', 'Sélectionnez un bloc pour l’éditer.'));
      return;
    }

    inspector.appendChild(el('p', 'camp-pane-title', LABELS[block.type] || block.type));
    var set = function (key) {
      return function (value) { block[key] = value; renderCanvas(); };
    };

    switch (block.type) {
      case 'header':
        inspector.appendChild(field('Nom affiché', input(block.title, set('title'))));
        inspector.appendChild(field('Sur-titre', input(block.tagline, set('tagline'))));
        inspector.appendChild(checkbox('Afficher le logo', block.logo !== false, set('logo')));
        inspector.appendChild(field('Alignement', select(block.align || 'left', ALIGNS, set('align'))));
        break;
      case 'heading':
        inspector.appendChild(field('Texte', input(block.text, set('text'), 'textarea')));
        inspector.appendChild(field('Alignement', select(block.align || 'left', ALIGNS, set('align'))));
        inspector.appendChild(field('Taille (px)', input(block.size || 26, function (v) {
          block.size = parseInt(v, 10) || 26; renderCanvas();
        }, 'number')));
        break;
      case 'text':
        inspector.appendChild(field('Contenu', input(htmlToLines(block.html), function (v) {
          block.html = linesToHtml(v); renderCanvas();
        }, 'textarea')));
        inspector.appendChild(el('p', 'camp-pane-hint', 'Une ligne vide sépare deux paragraphes.'));
        inspector.appendChild(field('Alignement', select(block.align || 'left', ALIGNS, set('align'))));
        break;
      case 'button':
        inspector.appendChild(field('Libellé', input(block.label, set('label'))));
        inspector.appendChild(field('Lien', input(block.url, set('url'))));
        inspector.appendChild(field('Alignement', select(block.align || 'left', ALIGNS, set('align'))));
        break;
      case 'image':
        inspector.appendChild(field('URL de l’image', input(block.src, set('src'))));
        inspector.appendChild(field('Texte alternatif', input(block.alt, set('alt'))));
        inspector.appendChild(field('Lien au clic', input(block.href, set('href'))));
        inspector.appendChild(field('Largeur (px)', input(block.width || 520, function (v) {
          block.width = parseInt(v, 10) || 520; renderCanvas();
        }, 'number')));
        inspector.appendChild(field('Alignement', select(block.align || 'center', ALIGNS, set('align'))));
        break;
      case 'list':
        inspector.appendChild(linesField('Éléments (un par ligne)', block.items, set('items')));
        inspector.appendChild(field('Puce', input(block.icon || '✓', set('icon'))));
        break;
      case 'offer':
        inspector.appendChild(field('Nom de l’offre', input(block.name, set('name'))));
        inspector.appendChild(field('Prix', input(block.price, set('price'))));
        inspector.appendChild(field('Période', input(block.period, set('period'))));
        inspector.appendChild(field('Description', input(block.description, set('description'), 'textarea')));
        inspector.appendChild(linesField('Inclusions (une par ligne)', block.features, set('features')));
        inspector.appendChild(field('Badge', input(block.badge, set('badge'))));
        inspector.appendChild(field('Libellé du bouton', input(block.cta_label, set('cta_label'))));
        inspector.appendChild(field('Lien du bouton', input(block.cta_url, set('cta_url'))));
        inspector.appendChild(checkbox('Mettre en avant', block.highlight, set('highlight')));
        break;
      case 'stats':
        (block.items || []).forEach(function (item, i) {
          inspector.appendChild(field('Chiffre ' + (i + 1), input(item.value, function (v) {
            item.value = v; renderCanvas();
          })));
          inspector.appendChild(field('Légende ' + (i + 1), input(item.label, function (v) {
            item.label = v; renderCanvas();
          })));
        });
        break;
      case 'quote':
        inspector.appendChild(field('Citation', input(block.text, set('text'), 'textarea')));
        inspector.appendChild(field('Auteur', input(block.author, set('author'))));
        break;
      case 'spacer':
        inspector.appendChild(field('Hauteur (px)', input(block.height || 24, function (v) {
          block.height = parseInt(v, 10) || 24; renderCanvas();
        }, 'number')));
        break;
      case 'footer':
        inspector.appendChild(field('Texte', input(htmlToLines(block.html), function (v) {
          block.html = linesToHtml(v); renderCanvas();
        }, 'textarea')));
        break;
      case 'divider':
        inspector.appendChild(el('p', 'camp-pane-hint', 'Ce bloc n’a pas de réglage.'));
        break;
    }

    if (AI && EDITABLE && ['heading', 'text', 'list', 'button', 'offer', 'quote', 'footer'].indexOf(block.type) !== -1) {
      var aiWrap = el('div', 'camp-inspector-ai');
      var instruction = input('', function () {}, 'text');
      instruction.placeholder = 'Ex : plus court, plus concret';
      aiWrap.appendChild(field('Réécrire avec l’IA', instruction));
      var go = el('button', 'admin-btn ghost tiny', '✨ Réécrire ce bloc');
      go.type = 'button';
      go.addEventListener('click', function () {
        go.disabled = true; go.textContent = 'Réécriture…';
        fetch(root.dataset.rewriteUrl, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ block: block, instruction: instruction.value })
        })
          .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
          .then(function (res) {
            go.disabled = false; go.textContent = '✨ Réécrire ce bloc';
            if (!res.ok) { alert(res.data.error || 'Réécriture impossible.'); return; }
            var i = blockIndex(block.id);
            if (i !== -1) {
              state.design.blocks[i] = res.data.block;
              state.selected = res.data.block.id;
              renderCanvas(); renderInspector(); markDirty();
            }
          })
          .catch(function () { go.disabled = false; go.textContent = '✨ Réécrire ce bloc'; });
      });
      aiWrap.appendChild(go);
      inspector.appendChild(aiWrap);
    }
  }

  function htmlToLines(html) {
    return String(html || '')
      .replace(/<\/p\s*>\s*<p[^>]*>/gi, '\n\n')
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<\/?p[^>]*>/gi, '')
      .replace(/<[^>]+>/g, '')
      .trim();
  }
  function linesToHtml(text) {
    return String(text || '').split(/\n{2,}/).map(function (para) {
      return '<p>' + esc(para.trim()).replace(/\n/g, '<br>') + '</p>';
    }).filter(function (p) { return p !== '<p></p>'; }).join('');
  }

  // ── palette ──────────────────────────────────────────────────────────────
  Array.prototype.forEach.call(document.querySelectorAll('[data-add]'), function (btn) {
    btn.addEventListener('click', function () {
      if (!EDITABLE) return;
      var maker = DEFAULTS[btn.dataset.add];
      if (!maker) return;
      var block = maker();
      block.id = uid();
      var at = state.selected ? blockIndex(state.selected) + 1 : state.design.blocks.length;
      state.design.blocks.splice(at, 0, block);
      state.selected = block.id;
      renderCanvas(); renderInspector(); markDirty();
    });
  });

  // Merge tags insert into whichever field was last focused.
  Array.prototype.forEach.call(document.querySelectorAll('[data-tag]'), function (btn) {
    btn.addEventListener('mousedown', function (ev) { ev.preventDefault(); });
    btn.addEventListener('click', function () {
      var target = state.lastFocusedField ||
        document.activeElement && document.activeElement.classList.contains('admin-input')
          ? document.activeElement : null;
      target = state.lastFocusedField || target;
      if (!target || !('selectionStart' in target)) return;
      var start = target.selectionStart || 0;
      var end = target.selectionEnd || 0;
      target.value = target.value.slice(0, start) + btn.dataset.tag + target.value.slice(end);
      target.dispatchEvent(new Event('input', { bubbles: true }));
      target.focus();
      target.setSelectionRange(start + btn.dataset.tag.length, start + btn.dataset.tag.length);
    });
  });

  // ── side tabs / views ────────────────────────────────────────────────────
  Array.prototype.forEach.call(document.querySelectorAll('.camp-side-tab'), function (tab) {
    tab.addEventListener('click', function () {
      Array.prototype.forEach.call(document.querySelectorAll('.camp-side-tab'), function (t) { t.classList.remove('active'); });
      Array.prototype.forEach.call(document.querySelectorAll('.camp-pane'), function (p) { p.classList.remove('active'); });
      tab.classList.add('active');
      var pane = document.querySelector('.camp-pane[data-pane="' + tab.dataset.pane + '"]');
      if (pane) pane.classList.add('active');
    });
  });

  var previewBox = document.getElementById('camp-preview');
  var previewFrame = document.getElementById('camp-preview-frame');
  var deviceBox = document.getElementById('camp-device');

  Array.prototype.forEach.call(document.querySelectorAll('.camp-view'), function (btn) {
    btn.addEventListener('click', function () {
      Array.prototype.forEach.call(document.querySelectorAll('.camp-view'), function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      var isPreview = btn.dataset.view === 'preview';
      canvas.hidden = isPreview;
      previewBox.hidden = !isPreview;
      deviceBox.hidden = !isPreview;
      if (isPreview) refreshPreview();
    });
  });

  Array.prototype.forEach.call(document.querySelectorAll('.camp-device-btn'), function (btn) {
    btn.addEventListener('click', function () {
      Array.prototype.forEach.call(document.querySelectorAll('.camp-device-btn'), function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      previewBox.classList.toggle('mobile', btn.dataset.device === 'mobile');
    });
  });

  function refreshPreview() {
    fetch(root.dataset.previewUrl, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        design: state.design,
        preheader: document.getElementById('camp-preheader').value
      })
    })
      .then(function (r) { return r.json(); })
      .then(function (d) { previewFrame.srcdoc = d.html || ''; })
      .catch(function () { previewFrame.srcdoc = '<p style="font-family:sans-serif;padding:24px">Aperçu indisponible.</p>'; });
  }

  // ── style pane ───────────────────────────────────────────────────────────
  function bindSetting(id, key, transform) {
    var node = document.getElementById(id);
    if (!node) return;
    node.value = state.design.settings[key];
    node.disabled = !EDITABLE;
    node.addEventListener('input', function () {
      state.design.settings[key] = transform ? transform(node.value) : node.value;
      if (key === 'width') {
        document.getElementById('camp-set-width-val').textContent = state.design.settings.width + ' px';
      }
      renderCanvas(); markDirty();
    });
  }
  bindSetting('camp-set-bg', 'bg');
  bindSetting('camp-set-surface', 'surface');
  bindSetting('camp-set-accent', 'accent');
  bindSetting('camp-set-width', 'width', function (v) { return parseInt(v, 10) || 600; });
  var widthVal = document.getElementById('camp-set-width-val');
  if (widthVal) widthVal.textContent = state.design.settings.width + ' px';

  // ── header fields ────────────────────────────────────────────────────────
  ['camp-name', 'camp-subject', 'camp-preheader', 'camp-replyto'].forEach(function (id) {
    var node = document.getElementById(id);
    if (!node) return;
    node.disabled = !EDITABLE;
    node.addEventListener('focus', function () { state.lastFocusedField = node; });
    node.addEventListener('input', markDirty);
  });

  var subjectInput = document.getElementById('camp-subject');
  var subjectCount = document.getElementById('camp-subject-count');
  function updateSubjectCount() {
    var n = subjectInput.value.length;
    subjectCount.textContent = n + ' caractères' + (n > 70 ? ' — risque de troncature en boîte de réception' : '');
    subjectCount.classList.toggle('warn', n > 70);
  }
  subjectInput.addEventListener('input', updateSubjectCount);
  updateSubjectCount();

  // ── AI ───────────────────────────────────────────────────────────────────
  var subjectAi = document.getElementById('camp-subject-ai');
  var ideasBox = document.getElementById('camp-subject-ideas');
  if (subjectAi) {
    subjectAi.addEventListener('click', function () {
      var briefNode = document.getElementById('camp-ai-brief');
      var brief = (briefNode && briefNode.value) || subjectInput.value ||
        'Prospection d’artisans du bâtiment pour PilotCore.';
      subjectAi.disabled = true;
      fetch(root.dataset.subjectsUrl, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ brief: brief })
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          subjectAi.disabled = false;
          if (!res.ok) { alert(res.data.error || 'Génération impossible.'); return; }
          ideasBox.innerHTML = '';
          (res.data.subjects || []).forEach(function (s) {
            var li = el('li');
            var b = el('button', 'camp-idea', s);
            b.type = 'button';
            b.addEventListener('click', function () {
              subjectInput.value = s;
              updateSubjectCount();
              markDirty();
              ideasBox.hidden = true;
            });
            li.appendChild(b);
            ideasBox.appendChild(li);
          });
          ideasBox.hidden = false;
        })
        .catch(function () { subjectAi.disabled = false; });
    });
  }

  var aiBtn = document.getElementById('camp-ai-generate');
  if (aiBtn) {
    aiBtn.addEventListener('click', function () {
      var brief = document.getElementById('camp-ai-brief').value.trim();
      var note = document.getElementById('camp-ai-note');
      if (!brief) { note.hidden = false; note.textContent = 'Décrivez ce que doit dire l’e-mail.'; return; }
      aiBtn.disabled = true;
      note.hidden = false;
      note.textContent = 'L’IA lit le site et rédige…';
      fetch(root.dataset.generateUrl, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          brief: brief,
          tone: document.getElementById('camp-ai-tone').value,
          goal: document.getElementById('camp-ai-goal').value,
          audience: readSegment()
        })
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          aiBtn.disabled = false;
          if (!res.ok) { note.textContent = res.data.error || 'Génération impossible.'; return; }
          var d = res.data;
          state.design = normaliseDesign(d.design);
          state.selected = null;
          if (d.subject) { subjectInput.value = d.subject; updateSubjectCount(); }
          if (d.preheader) document.getElementById('camp-preheader').value = d.preheader;
          if (d.name) document.getElementById('camp-name').value = d.name;
          renderCanvas(); renderInspector(); markDirty();
          note.textContent = 'E-mail généré. Relisez-le puis ajustez bloc par bloc.';
        })
        .catch(function (e) { aiBtn.disabled = false; note.textContent = 'Génération impossible : ' + e; });
    });
  }

  // ── audience ─────────────────────────────────────────────────────────────
  var segTrades = document.getElementById('seg-trades');
  var segStatuses = document.getElementById('seg-statuses');
  var segCities = document.getElementById('seg-cities');
  var segLimit = document.getElementById('seg-limit');
  var segExclude = document.getElementById('seg-exclude');
  var segListing = document.getElementById('seg-listing');

  function fillChecks(node, values) {
    var set = values || [];
    Array.prototype.forEach.call(node.querySelectorAll('input[type=checkbox]'), function (el) {
      el.checked = set.indexOf(el.value) !== -1;
    });
  }
  function readChecks(node) {
    return Array.prototype.map.call(
      node.querySelectorAll('input[type=checkbox]:checked'),
      function (el) { return el.value; }
    );
  }
  function setChecks(node, on) {
    Array.prototype.forEach.call(node.querySelectorAll('input[type=checkbox]'), function (el) {
      el.checked = !!on;
    });
    markDirty();
    refreshAudience();
  }
  function readSegment() {
    return {
      trades: readChecks(segTrades),
      statuses: readChecks(segStatuses),
      cities: segCities.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean),
      sources: [],
      exclude_contacted: segExclude.checked,
      with_listing: segListing.checked,
      limit: parseInt(segLimit.value, 10) || 200
    };
  }
  fillChecks(segTrades, state.segment.trades);
  fillChecks(segStatuses, state.segment.statuses);
  segCities.value = (state.segment.cities || []).join(', ');
  segLimit.value = state.segment.limit || 200;
  segExclude.checked = state.segment.exclude_contacted !== false;
  segListing.checked = state.segment.with_listing === true;

  [segTrades, segStatuses, segCities, segLimit, segExclude, segListing].forEach(function (node) {
    node.addEventListener('change', function () { markDirty(); refreshAudience(); });
  });
  var tradesAll = document.getElementById('seg-trades-all');
  var tradesNone = document.getElementById('seg-trades-none');
  if (tradesAll) tradesAll.addEventListener('click', function () { setChecks(segTrades, true); });
  if (tradesNone) tradesNone.addEventListener('click', function () { setChecks(segTrades, false); });
  if (!EDITABLE) {
    Array.prototype.forEach.call(
      document.querySelectorAll('.camp-audience input, #seg-trades-all, #seg-trades-none'),
      function (n) { n.disabled = true; }
    );
  }

  function refreshAudience() {
    fetch(root.dataset.audienceUrl, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ segment: readSegment() })
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        document.getElementById('seg-count').textContent = d.will_receive;
        var box = document.getElementById('seg-sample');
        box.innerHTML = '';
        (d.sample || []).forEach(function (p) {
          var li = el('li');
          li.innerHTML = '<strong>' + esc(p.name) + '</strong> · ' + esc(p.email) +
            (p.city ? ' · ' + esc(p.city) : '') +
            (p.has_listing ? ' <span class="camp-sample-tag">fiche</span>' : '');
          box.appendChild(li);
        });
        var counter = box.previousElementSibling;
        if (counter) {
          var span = counter.querySelector('.camp-audience-count span');
          if (span) span.textContent = 'destinataires sur ' + d.total + ' correspondants';
        }
      })
      .catch(function () {});
  }
  document.getElementById('seg-refresh').addEventListener('click', refreshAudience);

  // ── send ─────────────────────────────────────────────────────────────────
  // The server sends one batch per request and answers with what is left. It
  // may also answer "throttled": the mail host asked for a pause (LWS replies
  // « 421 … too many connections » when it has had enough). That is not an
  // error — nothing was lost, the remaining recipients are still pending — so
  // the loop waits the delay the server named and picks up on its own.
  var prepareBtn = document.getElementById('camp-prepare');
  var sendBtn = document.getElementById('camp-send');
  var pauseBtn = document.getElementById('camp-pause');
  var sendNote = document.getElementById('camp-send-note');
  var sendStats = document.getElementById('camp-send-stats');
  var progressBar = document.getElementById('camp-progress');
  var gaugePct = document.getElementById('camp-gauge-pct');
  var waitTimer = null;

  function note(text, tone) {
    if (!sendNote) return;
    sendNote.hidden = !text;
    sendNote.textContent = text || '';
    sendNote.className = 'camp-alert camp-alert--' + (tone || 'info');
  }

  function paintStats(s) {
    if (!s || !sendStats) return;
    ['sent', 'pending', 'failed'].forEach(function (key) {
      var cell = sendStats.querySelector('[data-k="' + key + '"]');
      if (cell) cell.textContent = s[key];
    });
    var done = typeof s.progress_pct === 'number'
      ? s.progress_pct
      : (s.recipients ? Math.round(100 * (s.recipients - s.pending) / s.recipients) : 0);
    if (progressBar) progressBar.style.width = done + '%';
    if (gaugePct) gaugePct.textContent = done + '%';
  }

  function stopSending() {
    state.sending = false;
    if (waitTimer) { clearTimeout(waitTimer); waitTimer = null; }
    sendBtn.disabled = false;
    pauseBtn.hidden = true;
  }

  prepareBtn.addEventListener('click', function () {
    prepareBtn.disabled = true;
    note('Constitution de la liste…');
    save();
    setTimeout(function () {
      fetch(root.dataset.prepareUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          prepareBtn.disabled = false;
          if (!res.ok) { note(res.data.error || 'Préparation impossible.', 'danger'); return; }
          note(res.data.added + ' destinataires ajoutés — ' + res.data.total +
               ' au total, prêts à recevoir.', 'ok');
          paintStats(res.data.stats);
        })
        .catch(function (e) { prepareBtn.disabled = false; note('Préparation impossible : ' + e, 'danger'); });
    }, 400);
  });

  sendBtn.addEventListener('click', function () {
    if (state.sending) return;
    if (!confirm('Lancer l’envoi réel de cette campagne ?')) return;
    state.sending = true;
    sendBtn.disabled = true;
    pauseBtn.hidden = false;
    sendLoop(0, 0);
  });

  pauseBtn.addEventListener('click', function () {
    stopSending();
    note('Envoi interrompu. Relancez quand vous voulez : la reprise part des destinataires restants.', 'warn');
  });

  /** Wait `seconds`, counting down in the note, then resume the loop. */
  function holdThenResume(seconds, sent, failed, reason) {
    var left = Math.max(5, seconds || 60);
    (function tick() {
      if (!state.sending) return;
      note('Le serveur d’e-mails limite le débit — ' + sent + ' e-mails déjà partis. ' +
           'Reprise automatique dans ' + left + ' s.' + (reason ? ' (' + reason + ')' : ''), 'warn');
      if (left <= 0) { sendLoop(sent, failed); return; }
      left -= 1;
      waitTimer = setTimeout(tick, 1000);
    })();
  }

  function sendLoop(sentSoFar, failedSoFar) {
    if (!state.sending) return;
    note('Envoi en cours — ' + sentSoFar + ' e-mails partis…');
    fetch(root.dataset.sendUrl, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          stopSending();
          note(res.data.error || 'Envoi interrompu.', 'danger');
          return;
        }
        var d = res.data;
        paintStats(d.stats);
        var sent = sentSoFar + d.sent;
        var failed = failedSoFar + d.failed;
        if (!state.sending) return;
        if (d.throttled) {
          holdThenResume(d.retry_after, sent, failed, d.throttle_reason);
          return;
        }
        if (d.done) {
          stopSending();
          note('Campagne envoyée : ' + sent + ' e-mails partis' +
               (failed ? ', ' + failed + ' en échec' : '') +
               '. Le rapport se remplit au fil des ouvertures.', 'ok');
          return;
        }
        setTimeout(function () { sendLoop(sent, failed); }, 600);
      })
      .catch(function (e) {
        stopSending();
        note('Envoi interrompu : ' + e, 'danger');
      });
  }

  // ── boot ─────────────────────────────────────────────────────────────────
  renderCanvas();
  renderInspector();
  window.addEventListener('beforeunload', function (ev) {
    if (saveState && saveState.classList.contains('camp-save--dirty')) {
      ev.preventDefault();
      ev.returnValue = '';
    }
  });
})();
