import { cn } from "./cn";

export function Pricing({ featured }: { featured: boolean }) {
  return (
    <section className="mx-auto max-w-5xl px-6 py-16">
      <h2 className="text-2xl font-semibold text-slate-900">Pricing</h2>
      <p className="mt-2 max-w-prose text-base text-slate-600">One plan per team size. Cancel any time.</p>
      <div className={cn("mt-8 grid gap-6 rounded-lg border border-slate-200 p-6 shadow-sm", featured && "border-blue-600")}>
        <button className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700">Start trial</button>
        <button className="rounded-md px-4 py-2 text-sm text-slate-700 hover:bg-slate-100">Contact sales</button>
      </div>
    </section>
  );
}
