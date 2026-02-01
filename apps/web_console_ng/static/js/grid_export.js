/**
 * Grid Export Utilities for P6T8
 *
 * Provides formula-safe export functions for AG Grid data.
 * Sanitization is IDENTICAL to Python server-side implementation.
 */

window.GridExport = (function () {
  'use strict';

  /**
   * Sanitize a cell value for export to prevent formula injection.
   * MUST produce IDENTICAL output to Python sanitize_for_export().
   *
   * @param {*} value - Cell value to sanitize
   * @returns {*} Sanitized value (strings may be prefixed with ')
   */
  function sanitizeForExport(value) {
    // Only sanitize strings - numbers, booleans, null pass through unchanged
    if (typeof value !== 'string') return value;

    // Strip leading whitespace and control characters to find first meaningful char
    // This prevents bypass via " =FORMULA" or "\t=FORMULA"
    const trimmed = value.replace(/^[\s\x00-\x1f]+/, '');
    if (trimmed.length === 0) return value; // All whitespace - safe

    const firstChar = trimmed[0];
    const dangerous = ['=', '+', '@', '\t', '\r', '\n'];

    // Check if first meaningful character is dangerous
    if (dangerous.includes(firstChar)) {
      return "'" + value; // Prepend quote to ORIGINAL value
    }

    // For '-', only allow if STRICTLY numeric (e.g., "-123.45", "-1.2E-5")
    // Block "-1+1", "-A1", etc. which could be formulas
    if (firstChar === '-') {
      // Strict numeric pattern: optional minus, digits, optional decimal, optional scientific notation
      // Must match Python version in libs/platform/security/sanitization.py
      const strictNumericRegex = /^-?\d+(\.\d+)?([eE][+-]?\d+)?$/;
      if (!strictNumericRegex.test(trimmed)) {
        return "'" + value; // Non-numeric negative - sanitize
      }
    }

    return value; // Safe value
  }

  /**
   * Convert grid data to CSV string with formula sanitization.
   *
   * @param {Object} gridApi - AG Grid API instance
   * @param {string[]} visibleColumns - List of column field names to export
   * @returns {string} CSV string
   */
  function gridToCsvString(gridApi, visibleColumns) {
    const rows = [];

    // Header row
    const headers = visibleColumns.map((col) => {
      const colDef = gridApi.getColumnDef(col);
      return colDef ? colDef.headerName || col : col;
    });
    rows.push(headers.map(escapeCsvField).join(','));

    // Data rows
    gridApi.forEachNodeAfterFilterAndSort((node) => {
      const row = visibleColumns.map((col) => {
        let value = node.data[col];
        value = sanitizeForExport(value);
        return escapeCsvField(value);
      });
      rows.push(row.join(','));
    });

    return rows.join('\n');
  }

  /**
   * Escape a field for CSV format.
   */
  function escapeCsvField(value) {
    if (value == null) return '';
    const str = String(value);
    // Quote if contains comma, quote, or newline
    if (str.includes(',') || str.includes('"') || str.includes('\n')) {
      return '"' + str.replace(/"/g, '""') + '"';
    }
    return str;
  }

  /**
   * Export grid to CSV file download.
   * Uses AG Grid's native export with custom processCellCallback for sanitization.
   *
   * @param {Object} gridApi - AG Grid API instance
   * @param {string} filename - Filename for download (without extension)
   * @param {string[]} excludeColumns - Columns to exclude from export
   */
  function exportToCsv(gridApi, filename, excludeColumns = []) {
    const params = {
      fileName: filename + '.csv',
      processCellCallback: (params) => {
        return sanitizeForExport(params.value);
      },
      columnKeys: getExportableColumns(gridApi, excludeColumns),
    };
    gridApi.exportDataAsCsv(params);
  }

  /**
   * Copy grid data to clipboard as CSV.
   * Uses custom implementation since AG Grid Community lacks copyToClipboard().
   *
   * @param {Object} gridApi - AG Grid API instance
   * @param {string[]} excludeColumns - Columns to exclude from export
   * @returns {Promise<number>} Number of rows copied
   */
  async function copyToClipboard(gridApi, excludeColumns = []) {
    const columns = getExportableColumns(gridApi, excludeColumns);
    const csvString = gridToCsvString(gridApi, columns);
    await navigator.clipboard.writeText(csvString);

    // Count rows (subtract 1 for header)
    const rowCount = csvString.split('\n').length - 1;
    return rowCount;
  }

  /**
   * Get list of columns eligible for export.
   *
   * @param {Object} gridApi - AG Grid API instance
   * @param {string[]} excludeColumns - Columns to exclude
   * @returns {string[]} List of column field names
   */
  function getExportableColumns(gridApi, excludeColumns = []) {
    const allColumns = gridApi.getColumnDefs();
    return allColumns
      .filter((col) => !excludeColumns.includes(col.field))
      .filter((col) => col.field) // Must have field
      .map((col) => col.field);
  }

  /**
   * Get current visible rows count (after filter/sort).
   *
   * @param {Object} gridApi - AG Grid API instance
   * @returns {number} Row count
   */
  function getVisibleRowCount(gridApi) {
    let count = 0;
    gridApi.forEachNodeAfterFilterAndSort(() => count++);
    return count;
  }

  /**
   * Get current filter model from grid.
   *
   * @param {Object} gridApi - AG Grid API instance
   * @returns {Object} Filter model
   */
  function getFilterModel(gridApi) {
    return gridApi.getFilterModel() || {};
  }

  /**
   * Get current sort model from grid.
   *
   * @param {Object} gridApi - AG Grid API instance
   * @returns {Array} Sort model
   */
  function getSortModel(gridApi) {
    const columnState = gridApi.getColumnState();
    return columnState
      .filter((col) => col.sort)
      .map((col) => ({
        colId: col.colId,
        sort: col.sort,
        sortIndex: col.sortIndex,
      }));
  }

  // Public API
  return {
    sanitizeForExport,
    gridToCsvString,
    exportToCsv,
    copyToClipboard,
    getExportableColumns,
    getVisibleRowCount,
    getFilterModel,
    getSortModel,
    escapeCsvField,
  };
})();
