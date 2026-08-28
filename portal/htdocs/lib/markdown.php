<?php
// Darstellungs-Helfer fuer die Views. Alles, was aus der Datenbank kommt,
// stammt mittelbar aus einem Sprachmodell und ist damit nicht
// vertrauenswuerdig: es geht entweder durch md() oder durch e().

require_once __DIR__ . '/Parsedown.php';

/**
 * Rendert Modell-Prosa als Markdown.
 *
 * Safe Mode schaltet die Markup-Verarbeitung ab, rohes HTML wird also
 * escaped statt durchgereicht, und Attribute wie href werden gefiltert
 * (javascript:-URLs fallen heraus).
 */
function md(?string $text): string {
    static $parser = null;
    if ($parser === null) {
        $parser = new Parsedown();
        $parser->setSafeMode(true);
        $parser->setBreaksEnabled(false);
    }
    $html = $parser->text((string)$text);

    // Externe Links oeffnen in neuem Tab, ohne Zugriff auf window.opener.
    // Der Ersatz greift nur auf Parsedowns eigener Ausgabe: href steht dort
    // immer als erstes Attribut, und rohes <a> aus dem Text ist zu diesem
    // Zeitpunkt bereits zu &lt;a escaped.
    return str_replace(
        '<a href="http',
        '<a target="_blank" rel="noopener noreferrer" href="http',
        $html);
}

/** HTML-Escaping fuer alles, was nicht als Markdown gerendert wird. */
function e(?string $value): string {
    return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

/** Zahl in deutscher Schreibweise; null wird zum Gedankenstrich. */
function money(?float $value, int $decimals = 2): string {
    return $value === null ? '—' : number_format($value, $decimals, ',', '.');
}

/**
 * Betrag mit Waehrungszeichen. Getrennte Funktionen statt eines Parameters:
 * so steht an jeder Aufrufstelle im Klartext, welche Waehrung gemeint ist --
 * die eine Verwechslung, die hier Geld kostet.
 */
function usd(?float $value, int $decimals = 2): string {
    return money($value, $decimals) . ' $';
}

function eur(?float $value, int $decimals = 2): string {
    return money($value, $decimals) . ' €';
}

/**
 * Klassen fuer einen Container mit gerenderter Modell-Prosa.
 *
 * Tailwinds Typography-Plugin ist hier nicht installiert -- prose-* waeren
 * wirkungslose Klassennamen -- und Preflight nimmt p, ul, ol und em jede
 * Auszeichnung. Die paar noetigen Regeln stehen deshalb als arbitrary
 * variants am Container. Sie stehen genau hier, weil md() genau hier steht:
 * wer Modell-Prosa rendert, braucht beides, und zwei Kopien derselben Kette
 * (eine je View) laufen unweigerlich auseinander.
 *
 * $extra nimmt auf, was zum Ort gehoert statt zur Prosa: Textfarbe und
 * Schriftgroesse. Die duerfen nicht hier stehen -- der Bear-Case-Kasten ist
 * bernsteinfarben, der Marktkontext grau, und eine feste Farbe in dieser
 * Kette wuerde einen von beiden ueberschreiben.
 */
function prose_classes(string $extra = ''): string {
    $classes = 'max-w-none'
        . ' [&_p]:mb-3 [&_p:last-child]:mb-0'
        . ' [&_ul]:mb-3 [&_ul]:list-disc [&_ul]:ps-5'
        . ' [&_ol]:mb-3 [&_ol]:list-decimal [&_ol]:ps-5'
        . ' [&_li]:mb-1'
        . ' [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:text-base [&_h2]:font-semibold'
        . ' [&_strong]:font-semibold [&_em]:italic'
        . ' [&_a]:font-medium [&_a]:text-blue-600 [&_a]:underline dark:[&_a]:text-blue-400'
        . ' [&_code]:rounded [&_code]:bg-gray-100 dark:[&_code]:bg-gray-700 [&_code]:px-1';
    $extra = trim($extra);
    return $extra === '' ? $classes : "$extra $classes";
}

/** Status eines Picks als Flowbite-Badge. Gibt fertiges HTML zurueck. */
function status_badge(?string $status): string {
    $map = [
        'OPEN'          => ['Offen', 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300'],
        'TARGET1'       => ['Target 1', 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300'],
        'STOP'          => ['Stop', 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300'],
        'TIME_STOP'     => ['Zeitstopp', 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-300'],
        'NOT_TRIGGERED' => ['Nicht ausgelöst', 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300'],
    ];
    // Ein unbekannter Status kommt aus der Datenbank und wird deshalb
    // escaped, statt als Klassenname oder Markup zu landen.
    [$label, $classes] = $map[(string)$status]
        ?? [(string)$status, 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300'];
    return '<span class="text-xs font-medium me-2 px-2.5 py-0.5 rounded-sm ' . $classes . '">'
        . e($label) . '</span>';
}
