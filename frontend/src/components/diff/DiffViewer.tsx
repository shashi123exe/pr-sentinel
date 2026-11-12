import React, { useMemo } from 'react';
import { AlertCircle, FileCode, Info } from 'lucide-react';
import type { FindingOut, ReviewFileOut } from '../../types/api';
import { SeverityBadge } from '../badges/SeverityBadge';

interface DiffViewerProps {
  file: ReviewFileOut;
  findings: FindingOut[];
  onSelectFinding?: (finding: FindingOut) => void;
}

interface ParsedDiffLine {
  type: 'context' | 'add' | 'del' | 'hunk' | 'meta';
  oldLineNumber: number | null;
  newLineNumber: number | null;
  content: string;
}

export const DiffViewer: React.FC<DiffViewerProps> = ({
  file,
  findings,
  onSelectFinding,
}) => {
  // Parse unified diff patch into structured lines
  const parsedLines = useMemo(() => {
    if (!file.patch || !file.patch.trim()) return [];

    const rawLines = file.patch.split('\n');
    const result: ParsedDiffLine[] = [];

    let currentOldLine = 0;
    let currentNewLine = 0;
    let hasEncounteredHunk = false;

    for (const line of rawLines) {
      if (line.startsWith('@@')) {
        // Standard unified diff hunk header: @@ -oldStart[,oldCount] +newStart[,newCount] @@ [heading]
        const match = line.match(/^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@/);
        if (match) {
          currentOldLine = parseInt(match[1], 10);
          currentNewLine = parseInt(match[2], 10);
          hasEncounteredHunk = true;
        }
        result.push({
          type: 'hunk',
          oldLineNumber: null,
          newLineNumber: null,
          content: line,
        });
      } else if (!hasEncounteredHunk) {
        // Header / metadata before first hunk (e.g. diff --git, --- a/..., +++ b/...)
        result.push({
          type: 'meta',
          oldLineNumber: null,
          newLineNumber: null,
          content: line,
        });
      } else if (line.startsWith('+')) {
        result.push({
          type: 'add',
          oldLineNumber: null,
          newLineNumber: currentNewLine,
          content: line.slice(1),
        });
        currentNewLine++;
      } else if (line.startsWith('-')) {
        result.push({
          type: 'del',
          oldLineNumber: currentOldLine,
          newLineNumber: null,
          content: line.slice(1),
        });
        currentOldLine++;
      } else if (line.startsWith('\\')) {
        // e.g. \ No newline at end of file
        result.push({
          type: 'meta',
          oldLineNumber: null,
          newLineNumber: null,
          content: line,
        });
      } else {
        // Context line
        const text = line.startsWith(' ') ? line.slice(1) : line;
        result.push({
          type: 'context',
          oldLineNumber: currentOldLine,
          newLineNumber: currentNewLine,
          content: text,
        });
        currentOldLine++;
        currentNewLine++;
      }
    }

    return result;
  }, [file.patch]);

  // Map findings by line number for this file
  const findingsByLine = useMemo(() => {
    const map = new Map<number, FindingOut[]>();
    for (const f of findings) {
      if (f.file_path === file.path) {
        const existing = map.get(f.line_start) || [];
        existing.push(f);
        map.set(f.line_start, existing);
      }
    }
    return map;
  }, [findings, file.path]);

  // Handle case where patch is absent or empty
  if (!file.patch || !file.patch.trim()) {
    return (
      <div className="diff-empty-container">
        <div className="diff-header-bar">
          <div className="flex-align-center gap-2">
            <FileCode size={16} className="text-muted" aria-hidden="true" />
            <span className="font-mono font-semibold">{file.path}</span>
          </div>
          <span className="badge badge-secondary">{file.status}</span>
        </div>
        <div className="diff-unavailable-message">
          <Info size={24} className="text-muted" aria-hidden="true" />
          <p className="font-medium">No diff patch available for this file.</p>
          {file.skipped_reason ? (
            <p className="text-sm text-muted">
              Skipped reason: <span className="font-mono">{file.skipped_reason}</span>
            </p>
          ) : (
            <p className="text-sm text-muted">
              File may be empty, binary, deleted, or diff contents are identical.
            </p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="diff-viewer-wrapper" role="region" aria-label={`Diff for ${file.path}`}>
      <div className="diff-header-bar">
        <div className="diff-file-info">
          <FileCode size={16} className="text-muted" aria-hidden="true" />
          <span className="font-mono font-semibold">{file.path}</span>
          {file.language && (
            <span className="badge badge-sm badge-secondary">{file.language}</span>
          )}
        </div>
        <div className="diff-stats">
          <span className="diff-add-stat">+{file.additions}</span>
          <span className="diff-del-stat">-{file.deletions}</span>
          {findings.length > 0 && (
            <span className="badge badge-sm badge-warning">
              {findings.length} {findings.length === 1 ? 'finding' : 'findings'}
            </span>
          )}
        </div>
      </div>

      <div className="diff-table-container">
        <table className="diff-table">
          <tbody>
            {(() => {
              const renderedFindingIds = new Set<number>();
              return parsedLines.map((line, idx) => {
                if (line.type === 'hunk') {
                  return (
                    <tr key={idx} className="diff-row diff-row-hunk">
                      <td colSpan={3} className="diff-cell-hunk">
                        {line.content}
                      </td>
                    </tr>
                  );
                }

                if (line.type === 'meta') {
                  return (
                    <tr key={idx} className="diff-row diff-row-meta">
                      <td colSpan={3} className="diff-cell-meta">
                        {line.content}
                      </td>
                    </tr>
                  );
                }

                // Check if any finding begins on this line (prefer newLineNumber, fallback to oldLineNumber on deletions)
                const lineNum = line.newLineNumber ?? line.oldLineNumber;
                const rawFindings = lineNum ? findingsByLine.get(lineNum) || [] : [];
                const matchingFindings = rawFindings.filter((f) => {
                  if (renderedFindingIds.has(f.id)) return false;
                  renderedFindingIds.add(f.id);
                  return true;
                });

                return (
                  <React.Fragment key={idx}>
                    <tr className={`diff-row diff-row-${line.type}`}>
                      <td className="diff-line-num old-num">
                        {line.oldLineNumber ?? ''}
                      </td>
                      <td className="diff-line-num new-num">
                        {line.newLineNumber ?? ''}
                      </td>
                      <td className="diff-cell-content">
                        <span className="diff-sign">
                          {line.type === 'add' ? '+' : line.type === 'del' ? '-' : ' '}
                        </span>
                        <span className="diff-text">{line.content || ' '}</span>
                      </td>
                    </tr>

                    {/* Inline Finding Markers if findings exist on this line */}
                    {matchingFindings.length > 0 && (
                      <tr className="diff-finding-row">
                        <td colSpan={3} className="diff-finding-cell">
                          {matchingFindings.map((finding) => (
                            <div
                              key={finding.id}
                              className={`diff-finding-card border-${finding.severity}`}
                              onClick={() => onSelectFinding?.(finding)}
                              role="button"
                              tabIndex={0}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                  e.preventDefault();
                                  onSelectFinding?.(finding);
                                }
                              }}
                              aria-label={`Finding: ${finding.title}, Severity: ${finding.severity}`}
                            >
                              <div className="diff-finding-top">
                                <div className="flex-align-center gap-2">
                                  <AlertCircle size={15} className={`text-${finding.severity}`} />
                                  <SeverityBadge severity={finding.severity} size="sm" />
                                  <span className="diff-finding-title">{finding.title}</span>
                                </div>
                                <span className="diff-finding-action">Inspect &rarr;</span>
                              </div>
                              <p className="diff-finding-explanation">
                                {finding.explanation.slice(0, 160)}
                                {finding.explanation.length > 160 ? '...' : ''}
                              </p>
                            </div>
                          ))}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              });
            })()}
          </tbody>
        </table>
      </div>
    </div>
  );
};
