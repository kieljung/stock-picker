<?php
declare(strict_types=1);

// Lesender Teil des Portals. Ein Front-Controller, vier Routen, serverseitig
// gerendert. Der Schutz ist Basic Auth im Apache (.htaccess), nicht hier.

require __DIR__ . '/lib/db.php';
require __DIR__ . '/lib/migrate.php';
require __DIR__ . '/lib/repo.php';
require __DIR__ . '/lib/markdown.php';

$config = require(getenv('PORTAL_CONFIG') ?: __DIR__ . '/lib/config.php');
ini_set('display_errors', '0');

try {
    $pdo = db_connect($config['db']);
    migrate($pdo);
} catch (Throwable $exc) {
    error_log((string)$exc . "\n", 3, $config['log']);
    http_response_code(500);
    exit('Datenbank nicht verfügbar.');
}

// /r/2026-08-09/CRSR  ->  ['r', '2026-08-09', 'CRSR']
// Der Pfad kommt aus dem Rewrite (?path=…) oder aus REQUEST_URI; ohne
// mod_rewrite -- etwa beim eingebauten Server -- traegt nur ?path=.
$path = $_GET['path'] ?? trim((string)parse_url((string)$_SERVER['REQUEST_URI'], PHP_URL_PATH), '/');
$segments = array_values(array_filter(explode('/', (string)$path), 'strlen'));

/**
 * Rendert eine View in das Layout.
 *
 * Die Variablen werden erst nach dem Merken von Titel und Pfad ausgepackt,
 * damit ein Schluessel aus $vars die eigenen Locals nicht ueberschreibt.
 * Fehlt die View-Datei, wird das eine gewoehnliche Ausnahme statt eines
 * nicht abfangbaren require-Fatals.
 */
function render(string $view, array $vars, array $config): void {
    $file = __DIR__ . "/lib/views/$view.php";
    if (!is_file($file)) {
        throw new RuntimeException("View '$view' fehlt");
    }
    $title = $vars['title'] ?? $config['title'];
    $site_title = $config['title'];
    extract($vars, EXTR_SKIP);

    ob_start();
    require $file;
    $content = ob_get_clean();
    require __DIR__ . '/lib/views/layout.php';
}

try {
    if (!$segments) {
        render('reports', [
            'reports'   => list_reports($pdo, (float)$config['account']),
            'overall'   => performance_overall($pdo),
            'last_push' => $pdo->query('SELECT MAX(at) FROM pushes')->fetchColumn() ?: null,
        ], $config);
    } elseif ($segments[0] === 'performance' && count($segments) === 1) {
        $from = $_GET['from'] ?? null;
        $to   = $_GET['to'] ?? null;
        render('performance', [
            'title'       => 'Performance',
            'overall'     => performance_overall($pdo, $from, $to),
            'by_setup'    => performance_by($pdo, 'setup', $from, $to),
            'by_catalyst' => performance_by($pdo, 'catalyst_bucket', $from, $to),
            'curve'       => cumulative_r($pdo, $from, $to),
            'from'        => $from, 'to' => $to,
        ], $config);
    } elseif ($segments[0] === 'r' && count($segments) === 2) {
        $report = get_report($pdo, $segments[1], (float)$config['account']);
        if (!$report) {
            http_response_code(404);
            exit('Kein Report für dieses Datum.');
        }
        render('report', ['title' => 'Report ' . $report['date'], 'report' => $report], $config);
    } elseif ($segments[0] === 'r' && count($segments) === 3) {
        $pick = get_pick($pdo, $segments[1], $segments[2], (float)$config['account']);
        if (!$pick) {
            http_response_code(404);
            exit('Kein Pick unter dieser Adresse.');
        }
        render('pick', ['title' => $pick['ticker'] . ' — ' . $segments[1],
                        'pick' => $pick, 'account' => (float)$config['account']], $config);
    } else {
        http_response_code(404);
        exit('Seite nicht gefunden.');
    }
} catch (Throwable $exc) {
    error_log((string)$exc . "\n", 3, $config['log']);
    http_response_code(500);
    exit('Unerwarteter Fehler. Details stehen im Log.');
}
