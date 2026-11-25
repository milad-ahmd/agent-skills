import { Order } from "./order";

export interface CartItem {
  sku: string;
  quantity: number;
}

export class Cart {
  items: CartItem[] = [];
}

export enum PaymentState {
  Pending = "pending, not yet sent",
  Voided = "voided",
}
