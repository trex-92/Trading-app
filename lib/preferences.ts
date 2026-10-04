import AsyncStorage from '@react-native-async-storage/async-storage';

/** Device-local preferences only; never synced to the backend. */
export const Prefs = {
  biometricLock: 'pref.biometricLock',
} as const;

export async function getBool(key: string): Promise<boolean> {
  return (await AsyncStorage.getItem(key)) === '1';
}

export async function setBool(key: string, value: boolean): Promise<void> {
  await AsyncStorage.setItem(key, value ? '1' : '0');
}
