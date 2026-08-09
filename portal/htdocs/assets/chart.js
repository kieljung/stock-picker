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

  /**
   * Haengt die R-Leiter neben dem Chart an dessen Preisskala.
   *
   * Die Leiter ist eigenes Markup und rechnete ihre Positionen bisher selbst
   * aus -- auf einem Massstab von Stop bis Target 2, waehrend der Chart auf
   * Tief bis Hoch der Kurshistorie skaliert. Zwei Skalen nebeneinander, die
   * nichts voneinander wissen: dieselbe Zahl stand links und rechts auf
   * verschiedenen Hoehen. Deshalb kommen die Positionen jetzt aus
   * priceToCoordinate() und damit aus derselben Quelle wie die Kerzen.
   *
   * Der zurueckgegebene Aufruf ist wiederholbar und muss es sein: die
   * Preisskala aendert sich beim Zoomen, beim Scrollen und bei jeder
   * Groessenaenderung des Fensters.
   */
  function ladderPlacer(container, series) {
    if (!container.id) { return null; }
    var ladder = document.querySelector('[data-chart-ladder="' + container.id + '"]');
    if (!ladder) { return null; }
    var rungs = Array.prototype.slice.call(ladder.querySelectorAll('[data-price]'));
    if (!rungs.length) { return null; }

    // Hoehe einer Beschriftungszeile plus etwas Luft. Darunter beruehren
    // sich zwei Texte.
    var MIN_GAP = 13;

    return function place() {
      var visible = [];

      rungs.forEach(function (rung) {
        var price = parseFloat(rung.dataset.price);
        var y = isFinite(price) ? series.priceToCoordinate(price) : null;
        if (y === null || y === undefined || isNaN(y)) {
          // Ausserhalb der Skala lieber nichts zeigen als etwas Falsches.
          rung.style.visibility = 'hidden';
          return;
        }
        rung.style.visibility = '';
        // Die Sprosse haengt an ihrer Unterkante, dort sitzt der Strich --
        // genau wie beim prozentualen Fallback aus dem PHP-Markup.
        rung.style.bottom = 'auto';
        rung.style.top = y + 'px';
        rung.style.transform = 'translateY(-100%)';
        visible.push({
          el: rung, y: y,
          prio: parseInt(rung.dataset.rungPriority, 10) || 1
        });
      });

      // Liegen zwei Niveaus dicht beieinander -- Entry und letzter Kurs sind
      // das am Tag des Picks der Normalfall --, ueberlagern sich ihre
      // Beschriftungen. Dann bleibt der Strich stehen, weil er die Lage
      // zeigt, und nur der nachrangige Text weicht.
      //
      // Wer weicht, entscheidet die Prioritaet und nicht die Hoehe: die
      // R-Zahl am Kurszeiger ist das Einzige, was diese Leiter zeigt und der
      // Chart nicht. Entry, Stop und Targets stehen dort schon als
      // Achsenbeschriftung -- sie hier ein zweites Mal zu zeigen ist der
      // verzichtbarere Teil.
      var claimed = [];
      visible.slice()
        .sort(function (a, b) { return (b.prio - a.prio) || (a.y - b.y); })
        .forEach(function (item) {
          var label = item.el.querySelector('[data-rung-label]');
          if (!label) { return; }
          var free = claimed.every(function (y) {
            return Math.abs(y - item.y) >= MIN_GAP;
          });
          label.style.visibility = free ? '' : 'hidden';
          if (free) { claimed.push(item.y); }
        });
    };
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
        // Der untere Rand haelt die Kerzen ueber dem Volumen, das in den
        // unteren 20 % der Flaeche liegt. Nicht mehr als 0,20: seit die
        // Targets in der Skala stecken, ist die Spanne groesser, und ein
        // groesserer Rand schiebt die Achse unter null -- eine Preisachse,
        // die -2,00 beschriftet, behauptet Unsinn.
        scaleMargins: { top: 0.05, bottom: 0.20 }
      },
      timeScale: { visible: !mini, borderColor: colors.grid, timeVisible: false },
      crosshair: { mode: mini ? LC.CrosshairMode.Hidden : LC.CrosshairMode.Normal },
      handleScroll: !mini,
      handleScale: !mini,
      autoSize: true
    });

    // Die vier Handelsniveaus muessen in die Autoskalierung, sonst zeichnet
    // die Bibliothek sie ausserhalb des sichtbaren Bereichs: Targets liegen
    // regelmaessig ueber dem hoechsten Kurs der Historie -- bei einem frischen
    // Pick sogar immer, weil Target 1 per Definition ueber dem Entry liegt und
    // der Entry ueber dem letzten Hoch. Ohne diesen Eingriff zeigt der Chart
    // Stop und Entry und verschweigt beide Ziele.
    var levels = [data.stop, data.entry, data.target1, data.target2]
      .filter(function (value) {
        return value !== null && value !== undefined && isFinite(+value);
      })
      .map(Number);

    var candles = chart.addSeries(LC.CandlestickSeries, {
      upColor: UP, downColor: DOWN,
      borderUpColor: UP, borderDownColor: DOWN,
      wickUpColor: UP, wickDownColor: DOWN,
      autoscaleInfoProvider: function (original) {
        var res = original();
        if (!res || !res.priceRange || !levels.length) { return res; }
        return {
          priceRange: {
            minValue: Math.min.apply(null, [res.priceRange.minValue].concat(levels)),
            maxValue: Math.max.apply(null, [res.priceRange.maxValue].concat(levels))
          },
          margins: res.margins
        };
      }
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

    var place = ladderPlacer(container, candles);
    if (place) {
      // Der erste Aufruf erst im naechsten Frame: unmittelbar nach setData()
      // steht das Layout noch nicht, priceToCoordinate() liefert dann null.
      requestAnimationFrame(place);
      chart.timeScale().subscribeVisibleLogicalRangeChange(place);
      if (window.ResizeObserver) {
        new ResizeObserver(place).observe(container);
      }
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
