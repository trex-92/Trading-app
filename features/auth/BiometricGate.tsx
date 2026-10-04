import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Pressable } from 'react-native';

import { ThemedText, ThemedView } from '@/components/themed';
import { Prefs, getBool } from '@/lib/preferences';
import { unlock } from '@/lib/providers/biometrics';

/** Locks the app behind biometrics when the user enabled it. Prompts once on launch. */
export function BiometricGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<'checking' | 'locked' | 'open'>('checking');

  const attempt = useCallback(async () => {
    setState((await unlock()) ? 'open' : 'locked');
  }, []);

  useEffect(() => {
    getBool(Prefs.biometricLock).then((enabled) => (enabled ? attempt() : setState('open')));
  }, [attempt]);

  if (state === 'open') return <>{children}</>;
  return (
    <ThemedView style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
      {state === 'locked' && (
        <Pressable onPress={attempt}><ThemedText themeColor="accent" type="bold">Unlock</ThemedText></Pressable>
      )}
    </ThemedView>
  );
}
