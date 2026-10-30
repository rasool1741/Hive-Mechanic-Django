(function () {
  if (!('serviceWorker' in navigator)) return
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/static/builder-js/service-worker.js').catch(function () {})
  })
})()
