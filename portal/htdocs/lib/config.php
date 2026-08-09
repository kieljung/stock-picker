<?php
// Der Dateiname der Datenbank wird beim Deployment gewuerfelt. Eine
// bekannt benannte SQLite-Datei unter htdocs ist die eine Nachlaessigkeit,
// die ein einzelner Konfigurationsfehler beim Hoster in einen
// vollstaendigen Datenabfluss verwandelt.
return [
    'db'      => __DIR__ . '/../data/portal.sqlite',
    'log'     => __DIR__ . '/../data/portal.log',
    'title'   => 'stock-picker',
    // Kontogroesse in EUR -- die Waehrung des Depots, nicht die der Kurse.
    // Die Positionsgroesse rechnet das Risiko je Stueck erst mit dem
    // Tageskurs nach EUR um; ohne Devisenkurs bleibt sie leer.
    'account' => 10000.0,
];
