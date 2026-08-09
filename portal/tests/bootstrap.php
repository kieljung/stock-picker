<?php
// Minimaler Test-Runner. Kein Framework: das Portal hat fuenf Tabellen und
// fuenf Endpunkte, PHPUnit waere mehr Konfiguration als Testcode.

$GLOBALS['tests_run'] = 0;
$GLOBALS['tests_failed'] = 0;

function assert_that(bool $condition, string $message): void {
    $GLOBALS['tests_run']++;
    if ($condition) {
        echo "  ok   $message\n";
        return;
    }
    $GLOBALS['tests_failed']++;
    echo "  FAIL $message\n";
}

function assert_equals($expected, $actual, string $message): void {
    assert_that($expected == $actual, $message . ' (erwartet: '
        . json_encode($expected) . ', war: ' . json_encode($actual) . ')');
}

function test_summary(): int {
    $run = $GLOBALS['tests_run'];
    $failed = $GLOBALS['tests_failed'];
    echo "\n$run Zusicherungen, $failed fehlgeschlagen\n";
    return $failed === 0 ? 0 : 1;
}
