export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-gradient-to-br from-background via-background to-brand/5">
      <div className="mb-6 text-center">
        <h1 className="text-lg font-semibold tracking-tight">Pokant</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Managed browser automation, powered by AI
        </p>
      </div>
      {children}
    </div>
  );
}
