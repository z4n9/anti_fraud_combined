import { Component, type ErrorInfo, type ReactNode } from "react";

interface AppErrorBoundaryProps {
  children: ReactNode;
}

interface AppErrorBoundaryState {
  failed: boolean;
}

export class AppErrorBoundary extends Component<
  AppErrorBoundaryProps,
  AppErrorBoundaryState
> {
  state: AppErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Risk Ledger rendering failed", error, info);
  }

  private recover = () => {
    window.location.assign("/analyst/new-analysis");
  };

  render() {
    if (!this.state.failed) return this.props.children;

    return (
      <main className="fatal-error" role="alert">
        <section className="fatal-error__card">
          <p className="eyebrow">Ошибка интерфейса</p>
          <h1>Не удалось показать результаты анализа</h1>
          <p>
            Сессия не удалена. Вернитесь к новому анализу и повторите действие.
          </p>
          <button type="button" onClick={this.recover}>
            Вернуться к загрузке
          </button>
        </section>
      </main>
    );
  }
}
