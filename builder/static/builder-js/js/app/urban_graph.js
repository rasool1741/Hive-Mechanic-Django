/* global cytoscape */
(function () {
  'use strict'
  const config = window.urbanGraphConfig
  if (!config || !window.cytoscape) return

  const status = document.getElementById('urban-graph-status')
  const preview = document.getElementById('urban-graph-preview')
  let graph = { nodes: [], edges: [], start: null, end: null }
  let cy

  function message (text) { status.textContent = text }
  function syncPreview () { preview.textContent = JSON.stringify(graph, null, 2) }
  function nodeData () { return graph.nodes.map(function (node) { return { data: { id: node.id, label: node.title || node.id } } }).concat(graph.edges.map(function (edge) { return { data: { id: edge.id, source: edge.source, target: edge.target, label: edge.condition ? JSON.stringify(edge.condition) : '' } } })) }
  function redraw () {
    if (cy) cy.destroy()
    cy = cytoscape({ container: document.getElementById('urban-graph-canvas'), elements: nodeData(), style: [
      { selector: 'node', style: { label: 'data(label)', 'text-valign': 'center', 'background-color': '#90caf9', 'border-width': 1, 'border-color': '#245' } },
      { selector: 'edge', style: { label: 'data(label)', 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', width: 2 } }
    ], layout: { name: 'breadthfirst', directed: true, padding: 20 } })
    syncPreview()
  }
  function selectedNodes () { return cy ? cy.nodes(':selected').map(function (n) { return n.id() }) : [] }
  function selectedNode () { const ids = selectedNodes(); return ids.length ? graph.nodes.find(function (n) { return n.id === ids[0] }) : null }
  function uniqueId () { let i = graph.nodes.length + 1; let id = 'stage-' + i; while (graph.nodes.some(function (n) { return n.id === id })) { i++; id = 'stage-' + i } return id }
  function csrf () { const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/); return match ? decodeURIComponent(match[1]) : '' }

  document.getElementById('urban-graph-add-node').onclick = function () {
    const id = uniqueId(); graph.nodes.push({ id: id, title: id, description: '', tools: [], conditions: {} }); redraw(); message('مرحله افزوده شد.')
  }
  document.getElementById('urban-graph-edit-node').onclick = function () {
    const node = selectedNode(); if (!node) return message('ابتدا یک مرحله را انتخاب کنید.')
    const title = window.prompt('عنوان مرحله', node.title || node.id); if (title !== null) node.title = title
    const description = window.prompt('توضیحات مرحله', node.description || ''); if (description !== null) node.description = description
    redraw()
  }
  document.getElementById('urban-graph-delete').onclick = function () {
    const ids = selectedNodes(); const edges = cy ? cy.edges(':selected').map(function (e) { return e.id() }) : []
    graph.nodes = graph.nodes.filter(function (n) { return ids.indexOf(n.id) < 0 })
    graph.edges = graph.edges.filter(function (e) { return ids.indexOf(e.source) < 0 && ids.indexOf(e.target) < 0 && edges.indexOf(e.id) < 0 })
    if (ids.indexOf(graph.start) >= 0) graph.start = null
    if (ids.indexOf(graph.end) >= 0) graph.end = null
    redraw()
  }
  document.getElementById('urban-graph-connect').onclick = function () {
    const ids = selectedNodes(); if (ids.length !== 2) return message('دو مرحله را با Ctrl انتخاب کنید.')
    const condition = window.prompt('شرط مسیر به‌صورت JSON (اختیاری)', '')
    let parsed = {}; if (condition) { try { parsed = JSON.parse(condition) } catch (e) { return message('شرط JSON معتبر نیست.') } }
    graph.edges.push({ id: 'edge-' + Date.now(), source: ids[0], target: ids[1], condition: parsed }); redraw()
  }
  document.getElementById('urban-graph-start').onclick = function () { const node = selectedNode(); if (node) { graph.start = node.id; redraw() } }
  document.getElementById('urban-graph-end').onclick = function () { const node = selectedNode(); if (node) { graph.end = node.id; redraw() } }
  document.getElementById('urban-graph-add-tool').onclick = function () {
    const node = selectedNode(); if (!node) return message('ابتدا یک مرحله را انتخاب کنید.')
    const type = document.getElementById('urban-graph-tool').value
    const raw = window.prompt('تنظیمات ابزار به‌صورت JSON (اختیاری)', '{}'); let settings = {}
    if (raw) { try { settings = JSON.parse(raw) } catch (e) { return message('تنظیمات JSON معتبر نیست.') } }
    node.tools = node.tools || []; node.tools.push({ type: type, settings: settings }); syncPreview()
  }
  document.getElementById('urban-graph-save').onclick = function () {
    fetch(config.url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify({ graph: graph }) })
      .then(function (response) { return response.json().then(function (data) { return { ok: response.ok, data: data } }) })
      .then(function (result) { if (!result.ok) throw new Error(result.data.error || 'ذخیره ناموفق بود.'); message(result.data.validation_errors && result.data.validation_errors.length ? 'Draft ذخیره شد؛ خطاهای انتشار باقی است.' : 'Draft ذخیره شد.') })
      .catch(function (error) { message(error.message) })
  }
  fetch(config.url).then(function (response) { return response.json() }).then(function (data) { graph = data.graph || graph; redraw() }).catch(function () { redraw(); message('بارگذاری گراف ناموفق بود.') })
})()
