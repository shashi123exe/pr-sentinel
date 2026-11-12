import React, { useState } from 'react';
import { Check, Copy } from 'lucide-react';

interface CodeBlockProps {
  code: string;
  startLine?: number;
  filename?: string;
  language?: string;
  maxHeight?: string;
}

export const CodeBlock: React.FC<CodeBlockProps> = ({
  code,
  startLine = 1,
  filename,
  language,
  maxHeight = '400px',
}) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard access error
    }
  };

  const lines = code.split('\n');

  return (
    <div className="code-block-wrapper">
      <div className="code-block-header">
        <div className="code-block-meta">
          {filename && <span className="code-block-filename">{filename}</span>}
          {language && <span className="code-block-lang">{language}</span>}
        </div>
        <button
          type="button"
          className="code-copy-btn"
          onClick={handleCopy}
          aria-label={copied ? 'Copied to clipboard' : 'Copy code to clipboard'}
        >
          {copied ? <Check size={14} className="text-success" /> : <Copy size={14} />}
          <span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>
      <div className="code-block-scroll" style={{ maxHeight }}>
        <pre className="code-pre">
          <code>
            {lines.map((line, idx) => {
              const lineNum = startLine + idx;
              return (
                <div key={idx} className="code-line">
                  <span className="code-line-number" aria-hidden="true">
                    {lineNum}
                  </span>
                  <span className="code-line-text">{line || ' '}</span>
                </div>
              );
            })}
          </code>
        </pre>
      </div>
    </div>
  );
};
