"use client";

import { Component, type ReactNode } from "react";

/**
 * A map that cannot start must not take the dashboard with it. WebGL is absent in
 * some browsers and locked-down desktops; MapLibre throws during render, and
 * without this boundary the whole page is replaced by Next's error screen.
 */
export class MapBoundary extends Component<
  { children: ReactNode; message: string },
  { failed: boolean }
> {
  constructor(props: { children: ReactNode; message: string }) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-slate-100 p-6 text-center">
          <p className="text-sm font-medium text-slate-700">{this.props.message}</p>
          <p className="text-xs text-slate-500">
            WebGL is niet beschikbaar in deze browser · WebGL is not available in this browser
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}
