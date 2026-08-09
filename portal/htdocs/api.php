<?php
declare(strict_types=1);

// Schreibender Teil des Portals. Drei Endpunkte, alle per POST, alle
// idempotent. Der Schutz ist Basic Auth im Apache (.htaccess), nicht hier.

require __DIR__ . '/lib/db.php';
require __DIR__ . '/lib/migrate.php';
require __DIR__ . '/lib/repo.php';
require __DIR__ . '/lib/validate.php';

header('Content-Type: application/json; charset=utf-8');

$config = require(getenv('PORTAL_CONFIG') ?: __DIR__ . '/lib/config.php');

function respond(int $status, array $body): never {
    http_response_code($status);
    echo json_encode($body, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function fail(int $status, string $error, array $details = []): never {
    respond($status, ['error' => $error, 'details' => $details]);
}

// Pfad kommt aus dem Rewrite (?path=…) oder aus REQUEST_URI.
$path = $_GET['path'] ?? trim((string)parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH), '/');
$path = preg_replace('#^api(\.php)?/#', '', (string)$path);
$method = $_SERVER['REQUEST_METHOD'];

try {
    $pdo = db_connect($config['db']);
    migrate($pdo);
} catch (Throwable $exc) {
    error_log((string)$exc . "\n", 3, $config['log']);
    fail(500, 'Datenbank nicht verfuegbar');
}

if ($path === 'health') {
    if ($method !== 'GET') {
        fail(405, 'Nur GET');
    }
    respond(200, [
        'ok'        => true,
        'reports'   => (int)$pdo->query('SELECT COUNT(*) FROM reports')->fetchColumn(),
        'picks'     => (int)$pdo->query('SELECT COUNT(*) FROM picks')->fetchColumn(),
        'bars'      => (int)$pdo->query('SELECT COUNT(*) FROM bars')->fetchColumn(),
        'last_push' => $pdo->query('SELECT MAX(at) FROM pushes')->fetchColumn() ?: null,
    ]);
}

if (!in_array($path, ['reports', 'bars', 'outcomes'], true)) {
    fail(404, "Unbekannter Pfad '$path'");
}
if ($method !== 'POST') {
    fail(405, 'Nur POST');
}

$raw = file_get_contents('php://input');
if (strlen($raw) > 8 * 1024 * 1024) {
    fail(413, 'Payload groesser als 8 MB');
}
$payload = json_decode($raw, true);
if (!is_array($payload)) {
    fail(400, 'Body ist kein JSON-Objekt: ' . json_last_error_msg());
}

try {
    if ($path === 'reports') {
        // Die Bars kommen aus yfinance, nicht aus dem Modell: sie taugen als
        // Referenz fuer den Abgleich des Entrys in validate_report().
        $errors = validate_report($payload, $payload['bars'] ?? []);
        if ($errors) {
            log_push($pdo, $path, strlen($raw), 422, implode('; ', $errors));
            fail(422, 'Report abgelehnt', $errors);
        }
        $result = save_report($pdo, $payload);
    } elseif ($path === 'bars') {
        $result = ['written' => save_bars($pdo, $payload)];
    } else {
        $result = ['updated' => save_outcomes($pdo, $payload)];
    }
} catch (Throwable $exc) {
    error_log((string)$exc . "\n", 3, $config['log']);
    log_push($pdo, $path, strlen($raw), 500, $exc->getMessage());
    fail(500, 'Schreiben fehlgeschlagen');
}

log_push($pdo, $path, strlen($raw), 200);
respond(200, ['ok' => true] + $result);
