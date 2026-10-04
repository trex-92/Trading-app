import { Stack } from 'expo-router';

import { SettingsScreen } from '@/features/settings/screens/SettingsScreen';

export default function Settings() {
  return (
    <>
      <Stack.Screen options={{ headerShown: true, title: 'Settings' }} />
      <SettingsScreen />
    </>
  );
}
