import { Component, type ReactNode } from 'react'

import { CrashPage } from './errors'

/** Last line of defence for errors thrown outside any route (the router handles its own). */
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    return this.state.failed ? <CrashPage /> : this.props.children
  }
}
