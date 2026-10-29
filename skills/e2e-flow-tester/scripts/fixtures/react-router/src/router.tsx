import { createBrowserRouter, Route, Routes } from "react-router-dom";
import Signup from "./pages/Signup";
import Cart from "./pages/Cart";

export const router = createBrowserRouter([
  { path: "/", element: <Home /> },
  { path: "/cart", element: <Cart /> },
  { path: "/orders/:orderId", lazy: () => import("./pages/Order") },
]);

export function LegacyRoutes() {
  return (
    <Routes>
      <Route path="/signup" element={<Signup />} />
      <Route path="/about" element={<About />} />
    </Routes>
  );
}
