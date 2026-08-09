<?php
// Front-Controller-Test gegen den eingebauten PHP-Server. Auth wird hier
// nicht geprueft: der eingebaute Server liest keine .htaccess, die Basic-Auth
// haengt am Apache des Hosters und wird beim Deployment geprueft.

require __DIR__ . '/bootstrap.php';
require __DIR__ . '/fixtures.php';

$root = __DIR__ . '/../htdocs';
$data = sys_get_temp_dir() . '/portal-test-' . getmypid();
mkdir($data . '/data', 0777, true);

// Konfiguration auf das Testverzeichnis umbiegen.
file_put_contents($data . '/config.php', '<?php return ' . var_export([
    'db'      => $data . '/data/test.sqlite',
    'log'     => $data . '/data/test.log',
    'title'   => 'test',
    'account' => 10000.0,
], true) . ';');
putenv('PORTAL_CONFIG=' . $data . '/config.php');

// Freien Port vom Betriebssystem geben lassen, statt 8123 zu raten: der Test
// soll nicht daran scheitern, dass auf der Maschine schon etwas lauscht.
$probe = stream_socket_server('tcp://127.0.0.1:0', $errno, $errstr);
$port = (int)explode(':', stream_socket_get_name($probe, false))[1];
fclose($probe);

$server = proc_open(
    escapeshellarg(PHP_BINARY) . " -S 127.0.0.1:$port -t " . escapeshellarg($root),
    [1 => ['file', '/dev/null', 'w'], 2 => ['file', $data . '/server.log', 'w']],
    $pipes,
    null,
    ['PORTAL_CONFIG' => $data . '/config.php'] + getenv()
);

// Aufraeumen auch dann, wenn eine Zusicherung mitten im Test hart abbricht.
register_shutdown_function(function () use (&$server, $data) {
    if (is_resource($server)) {
        proc_terminate($server);
        proc_close($server);
    }
    foreach (['data/test.sqlite', 'data/test.sqlite-wal', 'data/test.sqlite-shm',
              'data/test.log', 'server.log', 'config.php'] as $file) {
        @unlink($data . '/' . $file);
    }
    @rmdir($data . '/data');
    @rmdir($data);
});

function api(string $method, string $path, $body = null): array {
    global $port;
    $ch = curl_init("http://127.0.0.1:$port/api.php?path=$path");
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CUSTOMREQUEST  => $method,
        CURLOPT_HTTPHEADER     => ['Content-Type: application/json'],
        CURLOPT_TIMEOUT        => 10,
    ]);
    if ($body !== null) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body));
    }
    $response = curl_exec($ch);
    $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return [$status, json_decode((string)$response, true)];
}

// Warten, bis der Server antwortet -- eine feste Pause ist auf einer
// beschaeftigten Maschine mal zu kurz und sonst immer zu lang.
for ($try = 0; $try < 100; $try++) {
    $socket = @stream_socket_client("tcp://127.0.0.1:$port", $errno, $errstr, 0.1);
    if ($socket) {
        fclose($socket);
        break;
    }
    usleep(50000);
}

[$status, $body] = api('GET', 'health');
assert_equals(200, $status, 'health antwortet 200');
assert_equals(0, $body['reports'], 'Leere Datenbank meldet null Reports');

[$status, $body] = api('POST', 'reports', fixture_document());
assert_equals(200, $status, 'Gueltiger Report wird angenommen');
assert_equals(2, $body['picks'], 'Zwei Picks gespeichert');
assert_equals(false, $body['replaced'], 'Erster Push meldet keine Ersetzung');

[$status, $body] = api('POST', 'reports', fixture_document());
assert_equals(200, $status, 'Zweiter Push wird angenommen');
assert_equals(true, $body['replaced'], 'Zweiter Push meldet Ersetzung');

$broken = fixture_document();
$broken['picks'][0]['target1'] = $broken['picks'][0]['entry'] + 0.10;  // R:R weit unter 2
[$status, $body] = api('POST', 'reports', $broken);
assert_equals(422, $status, 'R:R unter 2 wird mit 422 abgelehnt');
assert_that(str_contains(implode(' ', $body['details']), 'R:R'), 'Fehlerdetails nennen R:R');

// Der Entry-Abgleich gegen die mitgelieferten Bars greift auch ueber die API.
$halluziniert = fixture_document();
$halluziniert['picks'][0]['entry'] = 90.00;
$halluziniert['picks'][0]['stop'] = 85.00;
$halluziniert['picks'][0]['target1'] = 101.00;
$halluziniert['picks'][0]['target2'] = 110.00;
[$status, $body] = api('POST', 'reports', $halluziniert);
assert_equals(422, $status, 'Entry weit weg vom Schlusskurs wird abgelehnt');
assert_that(str_contains(implode(' ', $body['details']), 'halluziniert'),
    'Fehlerdetails nennen den Halluzinationsverdacht');

[$status, $body] = api('POST', 'bars', ['XYZ' => [['2026-09-01', 1, 2, 0.5, 1.5, 100]]]);
assert_equals(200, $status, 'Bars werden angenommen');
assert_equals(1, $body['written'], 'Eine Bar geschrieben');

[$status, $body] = api('POST', 'outcomes',
    [['date' => '2026-08-09', 'ticker' => 'XYZ', 'status' => 'TARGET1', 'r_multiple' => 2.06]]);
assert_equals(200, $status, 'Ausgaenge werden angenommen');
assert_equals(1, $body['updated'], 'Ein Pick aktualisiert');

[$status, $body] = api('POST', 'reports', ['date' => '2026-08-09']);
assert_equals(422, $status, 'Unvollstaendiges Dokument wird mit 422 abgelehnt');

[$status] = api('GET', 'reports');
assert_equals(405, $status, 'GET auf reports wird abgelehnt');

[$status] = api('POST', 'unbekannt', []);
assert_equals(404, $status, 'Unbekannter Pfad antwortet 404');

// Jeder Push steht im Protokoll, die abgelehnten eingeschlossen.
[$status, $body] = api('GET', 'health');
assert_equals(1, $body['reports'], 'Nach den Pushes steht genau ein Report in der Datenbank');
assert_that(!empty($body['last_push']), 'health meldet den letzten Push');

exit(test_summary());
