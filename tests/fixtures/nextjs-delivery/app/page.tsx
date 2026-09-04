import "./globals.css";
import { Counter } from "./Counter";

export default function Page() {
  return (
    <main>
      <h1 data-testid="heading">DevOS Delivery Fixture</h1>
      <p data-testid="paragraph">Deterministic Next.js app for DevOS tests.</p>
      <Counter />
    </main>
  );
}
