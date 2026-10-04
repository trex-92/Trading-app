import { useEffect, useState } from 'react';
import { Pressable, Switch } from 'react-native';

import { Card, ThemedText } from '@/components/themed';
import { Row, Screen } from '@/features/bot/components';
import { Prefs, getBool, setBool } from '@/lib/preferences';
import { signOut } from '@/lib/providers/auth';
import { biometricsAvailable } from '@/lib/providers/biometrics';
import { useAuth } from '@/features/auth/AuthContext';
import { MarketsCard } from '@/features/strategies/components/MarketsCard';

export function SettingsScreen() {
  const { session } = useAuth();
  const [lock, setLock] = useState(false);
  const [canLock, setCanLock] = useState(false);

  useEffect(() => {
    getBool(Prefs.biometricLock).then(setLock);
    biometricsAvailable().then(setCanLock);
  }, []);

  return (
    <Screen>
      <ThemedText type="title">Settings</ThemedText>
      <Card>
        <ThemedText type="small" themeColor="textSecondary">Signed in as</ThemedText>
        <ThemedText>{session?.user.email}</ThemedText>
      </Card>
      <MarketsCard />
      {canLock && (
        <Card>
          <Row left={<ThemedText>Require Face ID / passcode</ThemedText>}
            right={<Switch value={lock} onValueChange={(v) => { setLock(v); setBool(Prefs.biometricLock, v); }} />} />
        </Card>
      )}
      <Pressable onPress={signOut}>
        <Card><ThemedText themeColor="negative" type="bold">Sign out</ThemedText></Card>
      </Pressable>
    </Screen>
  );
}
