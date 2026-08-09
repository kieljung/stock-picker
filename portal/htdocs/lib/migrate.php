<?php

const MIGRATIONS = [
    1 => <<<'SQL'
        CREATE TABLE reports (
            date           TEXT PRIMARY KEY,
            generated_at   TEXT,
            market_context TEXT,
            note           TEXT,
            rejected       TEXT,     -- JSON
            checklist      TEXT,     -- JSON
            disclaimer     TEXT,
            pushed_at      TEXT NOT NULL
        );

        CREATE TABLE picks (
            report_date     TEXT NOT NULL REFERENCES reports(date) ON DELETE CASCADE,
            ticker          TEXT NOT NULL,
            rank            INTEGER,
            name            TEXT,
            sector          TEXT,
            setup           TEXT,
            score_technical INTEGER,
            score_catalyst  INTEGER,
            score_risk      INTEGER,
            composite       INTEGER,
            score_rationale TEXT,    -- JSON
            entry           REAL, stop REAL, target1 REAL, target2 REAL,
            risk_reward     REAL,
            position_size   INTEGER,
            catalyst        TEXT,
            thesis          TEXT,
            technicals      TEXT,
            catalyst_detail TEXT,
            plan            TEXT,    -- JSON
            bear_case       TEXT,
            invalidation    TEXT,
            metrics         TEXT,    -- JSON
            sources         TEXT,    -- JSON
            status          TEXT NOT NULL DEFAULT 'OPEN',
            r_multiple      REAL,
            triggered_on    TEXT,
            closed_on       TEXT,
            evaluated_at    TEXT,
            PRIMARY KEY (report_date, ticker)
        );
        CREATE INDEX picks_ticker ON picks(ticker);
        CREATE INDEX picks_status ON picks(status);

        CREATE TABLE bars (
            ticker TEXT NOT NULL,
            date   TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            PRIMARY KEY (ticker, date)
        );

        CREATE TABLE pushes (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint  TEXT NOT NULL,
            at        TEXT NOT NULL,
            bytes     INTEGER,
            status    INTEGER,
            detail    TEXT
        );
        SQL,
];

function migrate(PDO $pdo): int {
    $pdo->exec('CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)');
    $current = (int)$pdo->query('SELECT COALESCE(MAX(version), 0) FROM schema_version')
        ->fetchColumn();

    $applied = 0;
    foreach (MIGRATIONS as $version => $sql) {
        if ($version <= $current) {
            continue;
        }
        $pdo->beginTransaction();
        $pdo->exec($sql);
        $stmt = $pdo->prepare('INSERT INTO schema_version (version, applied_at) VALUES (?, ?)');
        $stmt->execute([$version, gmdate('c')]);
        $pdo->commit();
        $applied++;
    }
    return $applied;
}
