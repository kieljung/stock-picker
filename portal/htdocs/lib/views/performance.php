<?php
/**
 * Performance: Datumsfilter, vier Kennzahlen, kumulierte R-Kurve und zwei
 * Aufschluesselungen. Erwartet $overall, $by_setup, $by_catalyst, $curve,
 * $from und $to aus index.php.
 *
 * Die Zahlen kommen fertig gerundet aus repo.php -- das ist das PHP-
 * Gegenstueck zu outcomes.summarize und muss review.py auf dieselben Werte
 * kommen. Diese View rechnet deshalb nichts nach, sie stellt nur dar.
 */

$card = 'p-6 bg-white border border-gray-200 rounded-lg shadow-sm '
      . 'dark:bg-gray-800 dark:border-gray-700';
$h2   = 'mb-3 text-xl font-semibold tracking-tight text-gray-900 dark:text-white';
$cell = 'px-4 py-3 whitespace-nowrap';

/** Nur ein echtes ISO-Datum landet wieder im Formularfeld. */
$iso = static fn($value): string =>
    (is_string($value) && preg_match('/\A\d{4}-\d{2}-\d{2}\z/', $value)) ? $value : '';

/** Vorzeichenbehaftete R-Zahl, gruen im Plus, rot im Minus. */
$r_cell = static function (float $value): string {
    $color = $value > 0 ? 'text-green-600 dark:text-green-400'
           : ($value < 0 ? 'text-red-600 dark:text-red-400'
                         : 'text-gray-900 dark:text-white');
    $sign = $value > 0 ? '+' : '';   // money() setzt nur das Minus
    return '<span class="font-medium ' . $color . '">'
        . e($sign . money($value, 2)) . ' R</span>';
};

$trades = (int)$overall['trades'];

$kpis = [
    ['Geschlossene Trades', (string)$trades, 'Target 1 oder Stop'],
    ['Trefferquote', money((float)$overall['win_rate'], 1) . ' %',
     'Anteil Target 1'],
    ['Erwartungswert', ($overall['expectancy_r'] > 0 ? '+' : '')
        . money((float)$overall['expectancy_r'], 2) . ' R',
     'je geschlossenem Trade'],
    ['Offen', (string)(int)$overall['open'],
     (int)$overall['not_triggered'] . ' nicht ausgelöst'],
];

// Die Kurve ist eine Liste von Trades; das Attribut traegt genau das, was
// cumulative_r() liefert. drawCurve() in chart.js legt daraus die Linie.
$curve_json = json_encode(array_values($curve),
    JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);

$total_r = $curve ? (float)end($curve)['cumulative'] : 0.0;
?>
<header class="mb-6">
  <h1 class="text-3xl font-bold tracking-tight text-gray-900 dark:text-white">Performance</h1>
  <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
    Dieselbe Auswertung wie <code>review.py</code>: geschlossen ist ein Trade,
    der Target 1 oder den Stop erreicht hat.
  </p>
</header>

<?php
// Das Formular schickt an die eigene Adresse zurueck. Steht der Pfad in der
// Query -- durch den Rewrite oder beim eingebauten Server --, muss er als
// verstecktes Feld mit, sonst ersetzt die Absendung die ganze Query und die
// Seite waere danach die Uebersicht.
$path_param = isset($_GET['path']) ? (string)$_GET['path'] : null;
?>
<form method="get" action=""
      class="mb-8 flex flex-wrap items-end gap-3 <?= $card ?>">
<?php if ($path_param !== null): ?>
  <input type="hidden" name="path" value="<?= e($path_param) ?>">
<?php endif; ?>
  <div>
    <label for="from" class="block mb-1 text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Von</label>
    <input type="date" id="from" name="from" value="<?= e($iso($from ?? null)) ?>"
           class="bg-gray-50 border border-gray-300 text-gray-900 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block w-full p-2.5 dark:bg-gray-700 dark:border-gray-600 dark:placeholder-gray-400 dark:text-white">
  </div>
  <div>
    <label for="to" class="block mb-1 text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Bis</label>
    <input type="date" id="to" name="to" value="<?= e($iso($to ?? null)) ?>"
           class="bg-gray-50 border border-gray-300 text-gray-900 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block w-full p-2.5 dark:bg-gray-700 dark:border-gray-600 dark:placeholder-gray-400 dark:text-white">
  </div>
  <button type="submit"
          class="text-white bg-blue-700 hover:bg-blue-800 focus:ring-4 focus:ring-blue-300 font-medium rounded-lg text-sm px-5 py-2.5 dark:bg-blue-600 dark:hover:bg-blue-700 dark:focus:ring-blue-800">
    Filtern
  </button>
<?php if ($iso($from ?? null) !== '' || $iso($to ?? null) !== ''): ?>
  <a href="/performance" class="text-sm font-medium text-blue-600 hover:underline dark:text-blue-400">
    Filter zurücksetzen
  </a>
<?php endif; ?>
  <p class="w-full text-xs text-gray-500 dark:text-gray-400">
    Gefiltert wird über das Report-Datum, nicht über den Ausstiegstag.
  </p>
</form>

<div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
<?php foreach ($kpis as [$label, $value, $hint]): ?>
  <div class="p-4 bg-white border border-gray-200 rounded-lg shadow-sm dark:bg-gray-800 dark:border-gray-700">
    <div class="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400"><?= e($label) ?></div>
    <div class="mt-1 text-2xl font-semibold text-gray-900 dark:text-white"><?= e($value) ?></div>
    <div class="mt-1 text-xs text-gray-500 dark:text-gray-400"><?= e($hint) ?></div>
  </div>
<?php endforeach; ?>
</div>

<?php if ($trades === 0): ?>
<div class="<?= $card ?> text-gray-600 dark:text-gray-300">
  Noch keine geschlossenen Trades. <code class="rounded bg-gray-100 px-1 dark:bg-gray-700">review.py</code>
  und <code class="rounded bg-gray-100 px-1 dark:bg-gray-700">publish.py --sync</code> schreiben Ausgänge,
  sobald das Beobachtungsfenster erreicht ist.
</div>
<?php else: ?>

<section class="mb-8">
  <h2 class="<?= $h2 ?>">
    Kumulierte R
    <span class="ms-2 text-base font-normal"><?= $r_cell($total_r) ?> gesamt</span>
  </h2>
  <div class="h-[320px] w-full rounded-lg border border-gray-200 dark:border-gray-700"
       data-curve='<?= e($curve_json) ?>'></div>
  <p class="mt-2 text-xs text-gray-500 dark:text-gray-400">
    Ein Punkt je Report-Datum, aufsummiert über die geschlossenen Trades in der
    Reihenfolge ihres Report-Datums. Schließen an einem Tag mehrere Trades,
    zeigt der Punkt den Stand nach dem letzten.
  </p>
</section>

<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
<?php foreach ([['Nach Setup', $by_setup], ['Nach Katalysator-Score', $by_catalyst]] as [$heading, $rows]): ?>
  <section>
    <h2 class="<?= $h2 ?>"><?= e($heading) ?></h2>
    <div class="relative overflow-x-auto border border-gray-200 rounded-lg shadow-sm dark:border-gray-700">
      <table class="w-full text-sm text-left rtl:text-right text-gray-500 dark:text-gray-400">
        <thead class="text-xs uppercase text-gray-700 bg-gray-50 dark:bg-gray-700 dark:text-gray-400">
          <tr>
            <th scope="col" class="<?= $cell ?>"><?= e($heading === 'Nach Setup' ? 'Setup' : 'Bucket') ?></th>
            <th scope="col" class="<?= $cell ?> text-right">Trades</th>
            <th scope="col" class="<?= $cell ?> text-right">Trefferquote</th>
            <th scope="col" class="<?= $cell ?> text-right">Erwartungswert</th>
          </tr>
        </thead>
        <tbody>
<?php if (!$rows): ?>
          <tr class="bg-white border-t border-gray-200 dark:bg-gray-800 dark:border-gray-700">
            <td class="<?= $cell ?>" colspan="4">Keine Zeilen im gewählten Zeitraum.</td>
          </tr>
<?php endif; ?>
<?php foreach ($rows as $name => $stats): ?>
          <tr class="bg-white border-t border-gray-200 dark:bg-gray-800 dark:border-gray-700">
            <th scope="row" class="<?= $cell ?> font-medium text-gray-900 dark:text-white">
              <?= e((string)$name) ?>
            </th>
            <td class="<?= $cell ?> text-right"><?= (int)$stats['trades'] ?></td>
            <td class="<?= $cell ?> text-right"><?= e(money((float)$stats['win_rate'], 1)) ?> %</td>
            <td class="<?= $cell ?> text-right"><?= $r_cell((float)$stats['expectancy_r']) ?></td>
          </tr>
<?php endforeach; ?>
        </tbody>
      </table>
    </div>
  </section>
<?php endforeach; ?>
</div>

<?php endif; ?>
