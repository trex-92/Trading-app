import { Link } from 'expo-router';

import { ThemedText, ThemedView } from '@/components/themed';

export default function CheckEmail() {
  return (
    <ThemedView style={{ flex: 1, justifyContent: 'center', padding: 24, gap: 16 }}>
      <ThemedText type="title">Check your email</ThemedText>
      <ThemedText>Confirm your address, then sign in.</ThemedText>
      <Link href="/login"><ThemedText themeColor="accent">Back to sign in</ThemedText></Link>
    </ThemedView>
  );
}
