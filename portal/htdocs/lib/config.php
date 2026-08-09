<?php
// Der Dateiname der Datenbank wird beim Deployment gewuerfelt. Eine
// bekannt benannte SQLite-Datei unter htdocs ist die eine Nachlaessigkeit,
// die ein einzelner Konfigurationsfehler beim Hoster in einen
// vollstaendigen Datenabfluss verwandelt.
return [
    'db'      => __DIR__ . '/../data/portal.sqlite',
    'log'     => __DIR__ . '/../data/portal.log',
    'title'   => 'stock-picker',
    'account' => 10000.0,   // Kontogroesse fuer die Positionsgroessen-Anzeige
];
