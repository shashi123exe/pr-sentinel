import React from 'react';

interface SkeletonProps {
  className?: string;
  width?: string;
  height?: string;
  count?: number;
}

export const Skeleton: React.FC<SkeletonProps> = ({
  className = '',
  width,
  height,
  count = 1,
}) => {
  const elements = Array.from({ length: count });

  return (
    <>
      {elements.map((_, i) => (
        <div
          key={i}
          className={`skeleton ${className}`}
          style={{ width, height }}
          aria-hidden="true"
        />
      ))}
    </>
  );
};

export const TableSkeleton: React.FC<{ rows?: number; cols?: number }> = ({
  rows = 5,
  cols = 5,
}) => {
  return (
    <div className="table-skeleton-container" aria-busy="true" aria-label="Loading table data">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="skeleton-row">
          {Array.from({ length: cols }).map((_, c) => (
            <div
              key={c}
              className="skeleton skeleton-cell"
              style={{ width: `${Math.floor(70 + ((r * 13 + c * 17) % 30))}%` }}
            />
          ))}
        </div>
      ))}
    </div>
  );
};
