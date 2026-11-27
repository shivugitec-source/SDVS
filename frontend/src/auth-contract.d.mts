export type OtpVerification =
  | { kind: "challenge"; nextChannel: "email" | "phone" }
  | {
      kind: "authenticated";
      accessToken: string;
      tokenType: "bearer";
      user: Record<string, unknown> & { role: string };
    };

export function parseOtpVerification(payload: unknown): OtpVerification;
