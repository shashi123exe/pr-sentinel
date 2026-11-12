import React from 'react';

interface ConfidenceMeterProps {
  confidence: number; // 0.0 to 1.0
  showLabel?: boolean;
  size?: 'sm' | 'md';
}

export const ConfidenceMeter: React.FC<ConfidenceMeterProps> = ({
  confidence,
  showLabel = true,
  size = 'md',
}) => {
  const clamped = Math.max(0, Math.min(1, confidence));
  const percent = Math.round(clamped * 100);

  let toneClass = 'meter-high';
  if (clamped < 0.6) {
    toneClass = 'meter-low';
  } else if (clamped < 0.85) {
    toneClass = 'meter-medium';
  }

  return (
    <div
      className={`confidence-meter-container ${size === 'sm' ? 'confidence-meter-sm' : ''}`}
      title={`Confidence: ${percent}%`}
      aria-label={`Confidence score ${percent} percent`}
    >
      <div className="confidence-track">
        <div
          className={`confidence-fill ${toneClass}`}
          style={{ width: `${percent}%` }}
        />
      </div>
      {showLabel && <span className="confidence-label">{percent}%</span>}
    </div>
  );
};
