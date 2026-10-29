export default function Signup() {
  return (
    <form onSubmit={(e) => e.preventDefault()}>
      <input type="email" aria-label="Email" />
      <input type="password" aria-label="Password" />
      <button>Create account</button>
    </form>
  );
}
