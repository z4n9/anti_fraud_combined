import type { SessionNotification } from "../types/notifications";
import { issueLevelLabel } from "../utils/dataQuality";

export function NotificationCenter({ notifications }: { notifications: SessionNotification[] }) {
  const critical = notifications.some(({ level }) => level === "critical");

  return (
    <details className="notification-center">
      <summary aria-label={`Уведомления текущей сессии: ${notifications.length}`} role="button">
        <span aria-hidden="true">!</span>
        {notifications.length > 0 && <b className={critical ? "is-critical" : ""}>{notifications.length}</b>}
      </summary>
      <div className="notification-center__panel">
        <header>
          <div><p className="eyebrow">Текущая сессия</p><h2>Уведомления</h2></div>
          <span>{notifications.length}</span>
        </header>
        {notifications.length ? (
          <ul>
            {notifications.map((notification) => (
              <li className={`notification-item notification-item--${notification.level}`} key={notification.id}>
                <span className="notification-item__level">{issueLevelLabel(notification.level)}</span>
                <strong>{notification.title}</strong>
                <p>{notification.description}</p>
                {(notification.technicalCode || notification.items?.length) && (
                  <details>
                    <summary>Технические детали</summary>
                    {notification.technicalCode && <code>{notification.technicalCode}</code>}
                    {notification.items && notification.items.length > 0 && <p>{notification.items.join(", ")}</p>}
                  </details>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="notification-center__empty">В текущей сессии нет сообщений, требующих внимания.</p>
        )}
      </div>
    </details>
  );
}
