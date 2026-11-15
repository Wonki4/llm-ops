export const TOKENS_PER_MILLION = 1_000_000;

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumSignificantDigits: 10,
});

export function pricePerMillion(perToken: number): string {
  return usd.format(perToken * TOKENS_PER_MILLION);
}

export function formatModelPrice(perToken: number | null | undefined): string {
  return perToken == null ? "—" : `${pricePerMillion(perToken)} / 1M`;
}
