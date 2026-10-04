import * as LocalAuthentication from 'expo-local-authentication';
import { Platform } from 'react-native';

export async function biometricsAvailable(): Promise<boolean> {
  if (Platform.OS === 'web') return false;
  return (await LocalAuthentication.hasHardwareAsync()) && (await LocalAuthentication.isEnrolledAsync());
}

/** Resolves true when the user passed (or biometrics are unavailable on this platform). */
export async function unlock(): Promise<boolean> {
  if (!(await biometricsAvailable())) return true;
  const r = await LocalAuthentication.authenticateAsync({ promptMessage: 'Unlock Trading Monitor' });
  return r.success;
}
