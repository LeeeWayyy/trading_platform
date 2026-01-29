// Sparkline renderer for AG Grid position rows
// Loaded as external script for CSP compliance
// SVG is generated server-side by sparkline_renderer.py

window.sparklineRenderer = function(params) {
    if (!params || !params.data || !params.data.sparkline_svg) {
        return document.createElement('span');
    }
    const wrapper = document.createElement('span');
    wrapper.innerHTML = params.data.sparkline_svg;
    return wrapper;
};
