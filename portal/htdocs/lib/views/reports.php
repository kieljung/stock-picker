<?php
/**
 * Uebersicht: vier Kennzahlen und darunter je Report eine Karte.
 * Erwartet $reports, $overall, $last_push.
 *
 * Jedes Feld aus der Datenbank laeuft hier durch e() -- der Anriss des
 * Marktkommentars ist abgeschnittene Modell-Prosa und taugt nicht als
 * Markdown, weil der Schnitt mitten in einer Auszeichnung liegen kann.
 */

$kpis = [
    ['Offene Picks',   (string)(int)$overall['open']],
    ['Trefferquote',   money((float)$overall['win_rate'], 1) . ' %'],
    ['Erwartungswert', money((float)$overall['expectancy_r'], 2) . ' R'],
    ['Letzter Push',   $last_push ? str_replace('T', ' ', substr((string)$last_push, 0, 16)) : '—'],
];
?>
<div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
<?php foreach ($kpis as [$label, $value]): ?>
  <div class="p-4 bg-white border border-gray-200 rounded-lg shadow-sm dark:bg-gray-800 dark:border-gray-700">
    <div class="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400"><?= e($label) ?></div>
    <div class="mt-1 text-2xl font-semibold text-gray-900 dark:text-white"><?= e($value) ?></div>
  </div>
<?php endforeach; ?>
</div>

<p class="mb-4 text-sm text-gray-500 dark:text-gray-400">
  <?= (int)$overall['trades'] ?> geschlossene Trades,
  <?= (int)$overall['not_triggered'] ?> nicht ausgelöst.
</p>

<?php if (!$reports): ?>
  <div class="p-6 bg-white border border-gray-200 rounded-lg dark:bg-gray-800 dark:border-gray-700 text-gray-500 dark:text-gray-400">
    Noch kein Report vorhanden.
  </div>
<?php endif; ?>

<div class="space-y-4">
<?php foreach ($reports as $report): ?>
<?php
    $date = (string)$report['date'];
    $context = trim((string)($report['market_context'] ?? ''));
    $excerpt = mb_strlen($context) > 240 ? mb_substr($context, 0, 240) . ' …' : $context;
?>
  <article class="p-6 bg-white border border-gray-200 rounded-lg shadow-sm dark:bg-gray-800 dark:border-gray-700">
    <h2 class="text-2xl font-bold tracking-tight text-gray-900 dark:text-white">
      <a href="/r/<?= e(rawurlencode($date)) ?>" class="hover:underline"><?= e($date) ?></a>
    </h2>

    <div class="mt-3 flex flex-wrap gap-2">
<?php foreach ($report['picks'] as $pick): ?>
      <a href="/r/<?= e(rawurlencode($date)) ?>/<?= e(rawurlencode((string)$pick['ticker'])) ?>"
         class="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-gray-200 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700">
        <span class="font-medium text-gray-900 dark:text-white"><?= e($pick['ticker']) ?></span>
        <span class="text-xs text-gray-500 dark:text-gray-400"><?= e((string)(int)$pick['composite']) ?></span>
        <?= status_badge($pick['status'] ?? null) ?>
      </a>
<?php endforeach; ?>
    </div>

<?php if ($excerpt !== ''): ?>
    <p class="mt-4 text-gray-600 dark:text-gray-300"><?= e($excerpt) ?></p>
<?php endif; ?>
  </article>
<?php endforeach; ?>
</div>
