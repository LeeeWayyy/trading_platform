// Sparkline renderer for AG Grid position rows
// Loaded as external script for CSP compliance

window.createSparklineSVG = function(data, width = 80, height = 20) {
    const values = Array.isArray(data) ? data.map(Number).filter(Number.isFinite) : [];
    if (values.length === 0) return '';
    if (values.length === 1) values.push(values[0]);

    const minVal = Math.min(...values);
    const maxVal = Math.max(...values);
    const span = (maxVal - minVal) || 1;
    const step = values.length > 1 ? (width / (values.length - 1)) : 0;

    const points = values.map((v, i) => {
        const x = (step * i).toFixed(2);
        const y = (height - ((v - minVal) / span) * height).toFixed(2);
        return `${x},${y}`;
    }).join(' ');

    const trendUp = values[values.length - 1] >= values[0];
    const color = trendUp ? 'var(--profit)' : 'var(--loss)';

    return (
        `<svg class="sparkline" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" ` +
        `xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false" style="color: ${color};">` +
        `<polyline fill="none" stroke="currentColor" stroke-width="1.5" points="${points}" />` +
        `</svg>`
    );
};

window.sparklineRenderer = function(params) {
    if (!params || !params.data) return document.createElement('span');
    const svg = params.data.sparkline_svg || window.createSparklineSVG(params.data.pnl_history || []);
    const wrapper = document.createElement('span');
    wrapper.innerHTML = svg || '';
    return wrapper;
};
