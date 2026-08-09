// Zeichnet Kerzen, Volumen, die vier Handelsniveaus und die Marker, die den
// Pick im Verlauf verorten. Die Daten kommen aus dem data-chart-Attribut des
// Containers -- kein zweiter Request.
//
// Zwei Varianten teilen sich denselben Code: die Detailansicht auf der
// Pick-Seite und die Miniatur auf der Report-Seite. Miniatur ist alles, was
// data-chart-variant="mini" oder die Klasse chart-mini traegt: kleinere
// Hoehe (aus dem Markup), keine Achsen, keine Marker-Beschriftung, kein
// Zoomen.
//
// API-Stand: lightweight-charts 5.2.0 (siehe node_modules/.../typings.d.ts).
// In v5 gibt es weder addCandlestickSeries() noch series.setMarkers() --
// Serien entstehen ueber addSeries(SeriesDefinition, options), Marker ueber
// das Plugin createSeriesMarkers(series, markers).

(function () {
  'use strict';

  var LC = window.LightweightCharts;

  var UP = '#16a34a';
  var DOWN = '#dc2626';

  // Alle gezeichneten Charts, damit der Theme-Umschalter sie nachfaerben kann.
  var charts = [];

  function themeColors() {
    var dark = document.documentElement.classList.contains('dark');
    return {
      background: dark ? '#111827' : '#ffffff',
      text:       dark ? '#9ca3af' : '#4b5563',
      grid:       dark ? '#1f2937' : '#e5e7eb'
    };
  }

  function isMini(container) {
    return container.dataset.chartVariant === 'mini'
        || container.classList.contains('chart-mini');
  }

  function parseData(container) {
    // data-chart ist die Vorgabe. data-pick wird mitgelesen, weil die
    // Report-Seite ihre Miniaturen historisch so benennt -- ein Attributname
    // soll nicht der Grund sein, dass ein Chart leer bleibt.
    var raw = container.dataset.chart || container.dataset.pick;
    if (!raw) { return null; }
    try {
      return JSON.parse(raw);
    } catch (err) {
      console.error('chart.js: data-chart ist kein gueltiges JSON', err);
      return null;
    }
  }

  /** Datum im Format YYYY-MM-DD um n Tage verschieben. */
  function shiftDate(date, days) {
    var t = Date.parse(date + 'T00:00:00Z');
    if (isNaN(t)) { return date; }
    return new Date(t + days * 86400000).toISOString().slice(0, 10);
  }

  /**
   * Faellt der Markerzeitpunkt ins Chartfenster?
   *
   * Die Bibliothek rastet Marker still auf den naechstgelegenen Balken ein
   * (MismatchDirection.NearestLeft/Right). Fuer den Report-Tag ist das
   * richtig -- er liegt regelmaessig einen Handelstag hinter der letzten Bar,
   * weil der Report vor der Eroeffnung entsteht. Fuer einen Earnings-Termin,
   * der Monate in der Zukunft liegt, ist es falsch: er saesse auf dem letzten
   * Kurstag und behauptete etwas, das dort nicht passiert ist. Deshalb eine
   * Woche Toleranz nach hinten und sonst nichts.
   */
  function withinWindow(time, first, last) {
    return typeof time === 'string' && time >= first && time <= shiftDate(last, 7);
  }

  function drawChart(container) {
    if (!LC) {
      console.error('chart.js: lightweight-charts ist nicht geladen');
      return;
    }
    if (container.dataset.chartDrawn) {
      return;
    }
    var data = parseData(container);
    if (!data || !data.bars || data.bars.length === 0) {
      return;
    }
    container.dataset.chartDrawn = '1';
    var mini = isMini(container);
    var colors = themeColors();

    var chart = LC.createChart(container, {
      layout: { background: { color: colors.background }, textColor: colors.text },
      grid: {
        vertLines: { color: colors.grid, visible: !mini },
        horzLines: { color: colors.grid, visible: !mini }
      },
      rightPriceScale: {
        visible: !mini,
        borderColor: colors.grid,
        scaleMargins: { top: 0.05, bottom: 0.25 }
      },
      timeScale: { visible: !mini, borderColor: colors.grid, timeVisible: false },
      crosshair: { mode: mini ? LC.CrosshairMode.Hidden : LC.CrosshairMode.Normal },
      handleScroll: !mini,
      handleScale: !mini,
      autoSize: true
    });

    var candles = chart.addSeries(LC.CandlestickSeries, {
      upColor: UP, downColor: DOWN,
      borderUpColor: UP, borderDownColor: DOWN,
      wickUpColor: UP, wickDownColor: DOWN
    });
    candles.setData(data.bars.map(function (b) {
      return { time: b.date, open: +b.open, high: +b.high, low: +b.low, close: +b.close };
    }));

    var volume = chart.addSeries(LC.HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      lastValueVisible: false,
      priceLineVisible: false
    });
    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    volume.setData(data.bars.map(function (b) {
      return {
        time: b.date, value: +b.volume,
        color: +b.close >= +b.open ? 'rgba(22,163,74,0.4)' : 'rgba(220,38,38,0.4)'
      };
    }));

    [
      { price: data.stop,    color: DOWN,      title: 'Stop' },
      { price: data.entry,   color: '#3b82f6', title: 'Entry' },
      { price: data.target1, color: UP,        title: 'Target 1' },
      { price: data.target2, color: '#4ade80', title: 'Target 2' }
    ].forEach(function (line) {
      if (line.price === null || line.price === undefined) { return; }
      candles.createPriceLine({
        price: +line.price, color: line.color, lineWidth: 1,
        lineStyle: LC.LineStyle.Dashed,
        axisLabelVisible: !mini,
        title: mini ? '' : line.title
      });
    });

    var first = data.bars[0].date;
    var last = data.bars[data.bars.length - 1].date;
    var markers = [];

    function addMarker(time, position, color, shape, text) {
      if (!withinWindow(time, first, last)) { return; }
      var marker = { time: time, position: position, color: color, shape: shape };
      if (!mini) { marker.text = text; }
      markers.push(marker);
    }

    addMarker(data.pickDate, 'belowBar', '#3b82f6', 'arrowUp', 'Pick · ' + data.composite);
    addMarker(data.triggeredOn, 'belowBar', '#3b82f6', 'circle', 'Entry');
    if (data.closedOn) {
      var win = data.status === 'TARGET1';
      addMarker(data.closedOn, 'aboveBar', win ? UP : DOWN, 'circle',
                win ? 'Target 1' : 'Stop');
    }
    addMarker(data.earnings, 'aboveBar', '#a855f7', 'square', 'Earnings');

    if (markers.length) {
      markers.sort(function (a, b) { return a.time < b.time ? -1 : (a.time > b.time ? 1 : 0); });
      LC.createSeriesMarkers(candles, markers);
    }

    // fitContent() legt die letzte Bar auf den rechten Rand. Der Marker am
    // Report-Tag sitzt genau dort, seine Beschriftung stuende damit halb
    // ausserhalb -- deshalb in der Detailansicht drei Balken Luft rechts.
    if (mini) {
      chart.timeScale().fitContent();
    } else {
      chart.timeScale().setVisibleLogicalRange({ from: -1, to: data.bars.length + 3 });
    }
    charts.push(chart);
    return chart;
  }

  /**
   * Zeichnet die kumulierte R-Kurve der Performance-Seite.
   *
   * Eine eigene Funktion und nicht ein Zweig in drawChart(): hier gibt es
   * weder Kerzen noch Volumen noch Handelsniveaus, nur eine Linie ueber
   * {time, value}. Die Daten stehen als JSON im data-curve-Attribut, in der
   * Form, die cumulative_r() in repo.php liefert.
   */
  function drawCurve(container) {
    if (!LC) {
      console.error('chart.js: lightweight-charts ist nicht geladen');
      return;
    }
    if (container.dataset.chartDrawn) {
      return;
    }
    var raw = container.dataset.curve;
    var points;
    try {
      points = JSON.parse(raw || '[]');
    } catch (err) {
      console.error('chart.js: data-curve ist kein gueltiges JSON', err);
      return;
    }
    if (!Array.isArray(points) || points.length === 0) {
      return;
    }
    container.dataset.chartDrawn = '1';
    var colors = themeColors();

    // Ein Punkt je Handelstag. Die Kurve zaehlt Trades, und an einem Tag
    // schliessen regelmaessig mehrere -- die Bibliothek verlangt aber streng
    // aufsteigende, eindeutige Zeitstempel und wirft sonst. Es gilt der
    // letzte Stand des Tages, also der Kurvenwert nach dem letzten Trade.
    var byDate = [];
    points.forEach(function (point) {
      var value = { time: String(point.date), value: +point.cumulative };
      if (byDate.length && byDate[byDate.length - 1].time === value.time) {
        byDate[byDate.length - 1] = value;
      } else {
        byDate.push(value);
      }
    });

    var chart = LC.createChart(container, {
      layout: { background: { color: colors.background }, textColor: colors.text },
      grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
      rightPriceScale: { borderColor: colors.grid, scaleMargins: { top: 0.1, bottom: 0.1 } },
      timeScale: { borderColor: colors.grid, timeVisible: false },
      crosshair: { mode: LC.CrosshairMode.Normal },
      autoSize: true
    });

    var line = chart.addSeries(LC.LineSeries, {
      color: '#3b82f6',
      lineWidth: 2,
      priceLineVisible: false,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
    });
    line.setData(byDate);

    // Die Nulllinie ist der Massstab der Kurve: darueber hat die Auswahl
    // Geld verdient, darunter gekostet.
    line.createPriceLine({
      price: 0, color: colors.text, lineWidth: 1,
      lineStyle: LC.LineStyle.Dotted, axisLabelVisible: false, title: ''
    });

    chart.timeScale().fitContent();
    charts.push(chart);
    return chart;
  }

  /** Faerbt alle Charts nach, wenn der Umschalter .dark an <html> aendert. */
  function repaint() {
    var colors = themeColors();
    charts.forEach(function (chart) {
      chart.applyOptions({
        layout: { background: { color: colors.background }, textColor: colors.text },
        grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
        rightPriceScale: { borderColor: colors.grid },
        timeScale: { borderColor: colors.grid }
      });
    });
  }

  function init() {
    document.querySelectorAll('[data-chart], [data-pick]').forEach(drawChart);
    document.querySelectorAll('[data-curve]').forEach(drawCurve);

    // Der Umschalter im Layout kippt die Klasse zur Laufzeit und laedt nicht
    // neu. Ohne diesen Beobachter behielte der Chart bis zum naechsten
    // Seitenaufruf die Farben des alten Themes.
    var last = document.documentElement.className;
    new MutationObserver(function () {
      if (document.documentElement.className !== last) {
        last = document.documentElement.className;
        repaint();
      }
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
  }

  // Kleiner oeffentlicher Griff: Seiten, die Container erst nach dem Laden
  // einhaengen, rufen drawAll() nach. Mehrfachaufrufe sind harmlos, weil
  // drawChart() bereits gezeichnete Container ueberspringt.
  window.portalChart = { drawAll: init, draw: drawChart, drawCurve: drawCurve };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
