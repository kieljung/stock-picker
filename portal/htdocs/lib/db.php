<?php

function db_connect(string $path): PDO {
    $pdo = new PDO('sqlite:' . $path, null, null, [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    // WAL, damit ein langer Push die Leseseite nicht blockiert; busy_timeout,
    // damit ein gleichzeitiger Request wartet statt "database is locked".
    if ($path !== ':memory:') {
        $pdo->exec('PRAGMA journal_mode = WAL');
    }
    $pdo->exec('PRAGMA busy_timeout = 5000');
    $pdo->exec('PRAGMA foreign_keys = ON');
    return $pdo;
}
