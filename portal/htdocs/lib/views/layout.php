<?php
/**
 * HTML-Grundgeruest. Erwartet $title, $content (fertiges HTML aus der View)
 * und $site_title. Wird ausschliesslich von render() in index.php geladen.
 */
?><!DOCTYPE html>
<html lang="de" class="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title><?= e($title) ?> · <?= e($site_title ?? '') ?></title>
<script>
// Dunkel ist der Standard und steht deshalb schon als Klasse im Markup.
// Dieses Skript laeuft vor dem Body und nimmt sie nur weg, wenn der Nutzer
// sich fuer hell entschieden hat -- so blitzt beim Laden kein helles Theme auf.
(function () {
  try {
    if (localStorage.theme === 'light') {
      document.documentElement.classList.remove('dark');
    }
  } catch (e) { /* localStorage gesperrt: dunkel bleibt dunkel */ }
})();
</script>
<link rel="stylesheet" href="/assets/app.css">
</head>
<body class="bg-gray-50 text-gray-900 dark:bg-gray-900 dark:text-gray-100">

<nav class="bg-white border-gray-200 dark:bg-gray-800 dark:border-gray-700 border-b">
  <div class="max-w-screen-xl flex flex-wrap items-center justify-between mx-auto p-4">
    <a href="/" class="flex items-center space-x-3 rtl:space-x-reverse">
      <span class="self-center text-xl font-semibold whitespace-nowrap dark:text-white"><?= e($site_title ?? '') ?></span>
    </a>
    <div class="flex items-center md:order-2 space-x-2 rtl:space-x-reverse">
      <button id="theme-toggle" type="button" aria-label="Theme umschalten"
              class="text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 focus:outline-none focus:ring-4 focus:ring-gray-200 dark:focus:ring-gray-700 rounded-lg text-sm p-2.5">
        <svg id="theme-toggle-dark-icon" class="hidden w-5 h-5" fill="currentColor" viewBox="0 0 20 20" aria-hidden="true">
          <path d="M17.293 13.293A8 8 0 016.707 2.707a8.001 8.001 0 1010.586 10.586z"></path>
        </svg>
        <svg id="theme-toggle-light-icon" class="hidden w-5 h-5" fill="currentColor" viewBox="0 0 20 20" aria-hidden="true">
          <path d="M10 2a1 1 0 011 1v1a1 1 0 11-2 0V3a1 1 0 011-1zm4 8a4 4 0 11-8 0 4 4 0 018 0zm-.464 4.95l.707.707a1 1 0 001.414-1.414l-.707-.707a1 1 0 00-1.414 1.414zm2.12-10.607a1 1 0 010 1.414l-.706.707a1 1 0 11-1.414-1.414l.707-.707a1 1 0 011.414 0zM17 11a1 1 0 100-2h-1a1 1 0 100 2h1zm-7 4a1 1 0 011 1v1a1 1 0 11-2 0v-1a1 1 0 011-1zM5.05 6.464A1 1 0 106.465 5.05l-.708-.707a1 1 0 00-1.414 1.414l.707.707zm1.414 8.486l-.707.707a1 1 0 01-1.414-1.414l.707-.707a1 1 0 011.414 1.414zM4 11a1 1 0 100-2H3a1 1 0 000 2h1z" fill-rule="evenodd" clip-rule="evenodd"></path>
        </svg>
      </button>
      <button data-collapse-toggle="navbar-main" type="button"
              class="inline-flex items-center p-2 w-10 h-10 justify-center text-sm text-gray-500 rounded-lg md:hidden hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-gray-200 dark:text-gray-400 dark:hover:bg-gray-700 dark:focus:ring-gray-600"
              aria-controls="navbar-main" aria-expanded="false">
        <span class="sr-only">Menü öffnen</span>
        <svg class="w-5 h-5" fill="none" viewBox="0 0 17 14" aria-hidden="true">
          <path stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M1 1h15M1 7h15M1 13h15"/>
        </svg>
      </button>
    </div>
    <div class="items-center justify-between hidden w-full md:flex md:w-auto md:order-1" id="navbar-main">
      <ul class="flex flex-col p-4 md:p-0 mt-4 font-medium border border-gray-100 rounded-lg bg-gray-50 md:space-x-8 rtl:space-x-reverse md:flex-row md:mt-0 md:border-0 md:bg-white dark:bg-gray-800 dark:border-gray-700 md:dark:bg-gray-800">
        <li><a href="/" class="block py-2 px-3 rounded-sm text-gray-900 hover:bg-gray-100 md:hover:bg-transparent md:hover:text-blue-700 md:p-0 dark:text-white md:dark:hover:text-blue-500 dark:hover:bg-gray-700 dark:hover:text-white md:dark:hover:bg-transparent">Reports</a></li>
        <li><a href="/performance" class="block py-2 px-3 rounded-sm text-gray-900 hover:bg-gray-100 md:hover:bg-transparent md:hover:text-blue-700 md:p-0 dark:text-white md:dark:hover:text-blue-500 dark:hover:bg-gray-700 dark:hover:text-white md:dark:hover:bg-transparent">Performance</a></li>
      </ul>
    </div>
  </div>
</nav>

<main class="max-w-screen-xl mx-auto p-4">
<?= $content ?>
</main>

<footer class="max-w-screen-xl mx-auto p-4 text-xs text-gray-500 dark:text-gray-400">
  Keine Anlageberatung. Alle Angaben ohne Gewähr.
</footer>

<script src="/assets/flowbite.min.js"></script>
<script src="/assets/lightweight-charts.standalone.production.js"></script>
<script src="/assets/chart.js"></script>
<script>
(function () {
  var toggle = document.getElementById('theme-toggle');
  var darkIcon = document.getElementById('theme-toggle-dark-icon');
  var lightIcon = document.getElementById('theme-toggle-light-icon');
  if (!toggle) { return; }

  function paint() {
    var dark = document.documentElement.classList.contains('dark');
    // Sichtbar ist immer das Symbol fuer das, was der Klick bringt.
    lightIcon.classList.toggle('hidden', !dark);
    darkIcon.classList.toggle('hidden', dark);
  }
  paint();

  toggle.addEventListener('click', function () {
    var dark = document.documentElement.classList.toggle('dark');
    try { localStorage.theme = dark ? 'dark' : 'light'; } catch (e) { /* egal */ }
    paint();
  });
})();
</script>
</body>
</html>
