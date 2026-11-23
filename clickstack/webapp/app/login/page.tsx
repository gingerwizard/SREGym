export const dynamic = "force-dynamic";

/** One field: the shared password. The form posts to /api/login, which sets the cookie and redirects. */
export default async function Login({ searchParams }: { searchParams: Promise<{ next?: string; error?: string }> }) {
  const { next = "/", error } = await searchParams;
  return (
    <main className="login">
      <form method="post" action="/api/login" className="card">
        <h1>SREGym × ClickStack results</h1>
        <p className="muted">This report is shared by password.</p>
        <input type="hidden" name="next" value={next.startsWith("/") ? next : "/"} />
        <label>
          Password <input type="password" name="password" autoFocus autoComplete="current-password" />
        </label>
        <button type="submit">Open the report</button>
        {error ? <p className="bad">That password was not accepted.</p> : null}
      </form>
    </main>
  );
}
