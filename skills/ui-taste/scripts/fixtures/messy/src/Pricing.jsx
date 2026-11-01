export function Pricing() {
  return (
    <section className="bg-gradient-to-r from-purple-600 to-blue-500 text-center py-[70px]">
      <h2 className="text-[34px] font-bold text-white">🚀 Simple pricing</h2>
      <p className="text-[#6b7280] mt-[13px] text-center">✨ No hidden fees</p>
      <div className="grid gap-[18px] rounded-[10px] shadow-[0_4px_14px_rgba(0,0,0,0.12)] text-center">
        <div className="p-[22px] bg-slate-50 text-gray-700 rounded-2xl z-[999]">💎 Pro plan</div>
        <button className="bg-indigo-500 hover:bg-violet-600 text-[15px] px-[18px] !rounded-full text-center">Get started</button>
        <button className="bg-sky-500 text-white text-center">Talk to sales</button>
      </div>
      <p style={{ color: '#888', backgroundColor: '#f4f4f4', fontSize: 13, padding: 5 }}>Billed yearly</p>
    </section>
  );
}
