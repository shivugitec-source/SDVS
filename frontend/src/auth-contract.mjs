export function parseOtpVerification(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("The server returned an invalid OTP verification response.");
  }

  const result = payload;
  if (result.verified !== true || typeof result.both_channels_verified !== "boolean") {
    throw new Error("The server returned an invalid OTP verification response.");
  }

  if (result.both_channels_verified) {
    if (
      typeof result.access_token !== "string" ||
      result.access_token.trim() === "" ||
      result.token_type !== "bearer" ||
      !result.user ||
      typeof result.user !== "object" ||
      Array.isArray(result.user) ||
      typeof result.user.role !== "string" ||
      result.user.role.trim() === ""
    ) {
      throw new Error("The server returned an incomplete authenticated session.");
    }
    return {
      kind: "authenticated",
      accessToken: result.access_token,
      tokenType: "bearer",
      user: result.user
    };
  }

  if ("access_token" in result || "token_type" in result) {
    throw new Error("The server returned a session token before both channels were verified.");
  }
  if (result.next_channel !== "email" && result.next_channel !== "phone") {
    throw new Error("The server returned an invalid next verification channel.");
  }

  return { kind: "challenge", nextChannel: result.next_channel };
}
