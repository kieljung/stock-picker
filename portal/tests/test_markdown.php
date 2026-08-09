<?php
// Die Prosa im Portal stammt aus einem Sprachmodell. Diese Datei haelt fest,
// dass sie beim Rendern nicht zu Markup werden kann.

require __DIR__ . '/bootstrap.php';
require __DIR__ . '/../htdocs/lib/markdown.php';

// --- md(): rohes HTML wird escaped -----------------------------------------

$html = md('Vorher <script>alert(1)</script> nachher');
assert_that(!str_contains($html, '<script'), 'md() laesst kein <script>-Tag durch');
assert_that(str_contains($html, '&lt;script&gt;'), 'md() escaped das Tag sichtbar');

$html = md('<img src=x onerror="alert(1)">');
assert_that(!str_contains($html, '<img'), 'md() laesst kein <img>-Tag durch');
// Das Event-Attribut steht nur noch als escapter Text da, nicht als Attribut.
assert_that(str_contains($html, '&lt;img src=x onerror='),
    'md() zeigt das Event-Attribut als Text statt es zu setzen');

// Auch ueber die Markdown-Syntax kommt kein Skript herein.
$html = md('[klick](javascript:alert(1))');
assert_that(!str_contains($html, 'href="javascript:'), 'md() blockt javascript:-Links');

// --- md(): Absaetze, Links, Listen ------------------------------------------

$html = md("Erster Absatz.\n\nZweiter Absatz.");
assert_that(substr_count($html, '<p>') === 2, 'md() rendert zwei Absaetze');

$html = md('Siehe [Beispiel](https://example.com/x) dazu.');
assert_that(str_contains($html, 'href="https://example.com/x"'), 'md() rendert Links');
assert_that(str_contains($html, 'target="_blank"'), 'http-Links bekommen target=_blank');
assert_that(str_contains($html, 'rel="noopener noreferrer"'), 'http-Links bekommen rel=noopener noreferrer');

$html = md('Siehe [Beispiel](http://example.com) dazu.');
assert_that(str_contains($html, 'target="_blank"'), 'auch http ohne s bekommt target=_blank');

// Ein relativer Link bleibt im selben Tab.
$html = md('Siehe [Report](/r/2026-08-09) dazu.');
assert_that(str_contains($html, 'href="/r/2026-08-09"'), 'md() rendert relative Links');
assert_that(!str_contains($html, 'target="_blank"'), 'relative Links bleiben im selben Tab');

$html = md("- eins\n- zwei\n- drei");
assert_that(str_contains($html, '<ul>'), 'md() rendert eine Liste');
assert_that(substr_count($html, '<li>') === 3, 'md() rendert drei Listenpunkte');

$html = md("1. eins\n2. zwei");
assert_that(str_contains($html, '<ol>'), 'md() rendert eine nummerierte Liste');

assert_equals('', md(null), 'md(null) liefert einen leeren String');

// --- e() --------------------------------------------------------------------

$escaped = e('<a href="x">&\'</a>');
assert_that(!str_contains($escaped, '<'), 'e() escaped spitze Klammern');
assert_that(str_contains($escaped, '&lt;a href=&quot;x&quot;&gt;'), 'e() escaped doppelte Anfuehrungszeichen');
assert_that(str_contains($escaped, '&#039;'), 'e() escaped einfache Anfuehrungszeichen');
assert_equals('', e(null), 'e(null) liefert einen leeren String');

// --- money() ----------------------------------------------------------------

assert_equals('—', money(null), 'money(null) liefert einen Gedankenstrich');
assert_equals('42,80', money(42.80), 'money() rundet auf zwei Stellen mit Komma');
assert_equals('1.234,50', money(1234.5), 'money() setzt den Tausenderpunkt');
assert_equals('66,7', money(66.66666, 1), 'money() beachtet die Stellenangabe');

// --- status_badge() ---------------------------------------------------------

assert_that(str_contains(status_badge('TARGET1'), 'Target 1'), 'status_badge kennt TARGET1');
assert_that(str_contains(status_badge('NOT_TRIGGERED'), 'Nicht ausgelöst'), 'status_badge kennt NOT_TRIGGERED');
$badge = status_badge('<script>alert(1)</script>');
assert_that(!str_contains($badge, '<script'), 'status_badge escaped einen unbekannten Status');

exit(test_summary());
