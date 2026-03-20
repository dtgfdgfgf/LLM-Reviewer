export function CommandRail({ children }) {
  return (
    <aside className="min-h-0 overflow-y-auto">
      <div className="flex flex-col gap-4 lg:sticky lg:top-6">{children}</div>
    </aside>
  );
}
