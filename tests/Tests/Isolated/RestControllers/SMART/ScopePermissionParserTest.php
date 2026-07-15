<?php

/**
 * Mixed SMART v1/v2 scope parsing regressions.
 *
 * @package OpenEMR
 * @license https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\RestControllers\SMART;

use OpenEMR\Common\Auth\OpenIDConnect\Repositories\ScopeRepository;
use OpenEMR\RestControllers\SMART\ScopePermissionParser;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

class ScopePermissionParserTest extends TestCase
{
    protected function setUp(): void
    {
        $GLOBALS['disable_translation'] = true;
    }

    protected function tearDown(): void
    {
        unset($GLOBALS['disable_translation']);
    }

    public static function mixedObservationScopeOrders(): array
    {
        return [
            'v1 then v2' => [[
                'user/Observation.read',
                'user/Observation.rs',
            ]],
            'v2 then v1' => [[
                'user/Observation.rs',
                'user/Observation.read',
            ]],
        ];
    }

    #[DataProvider('mixedObservationScopeOrders')]
    public function testMixedObservationFormatsRetainTheirOwnActions(array $scopes): void
    {
        $parsed = $this->parser()->parseScopes($scopes);

        $this->assertSame('user', $parsed['Observation']['context']);
        $this->assertSame(
            [
                'v1' => ['r', 's'],
                'v2' => ['r', 's'],
            ],
            $parsed['Observation']['actionsByVersion']
        );
    }

    public function testSingleFormatDoesNotAcquireAnUnrequestedVersion(): void
    {
        $v1 = $this->parser()->parseScopes(['user/Observation.read']);
        $v2 = $this->parser()->parseScopes(['user/Observation.rs']);

        $this->assertSame(['v1' => ['r', 's']], $v1['Observation']['actionsByVersion']);
        $this->assertSame(['v2' => ['r', 's']], $v2['Observation']['actionsByVersion']);
    }

    public function testMixedFormatsKeepDisjointActionsSeparated(): void
    {
        $parsed = $this->parser()->parseScopes([
            'user/Observation.read',
            'user/Observation.cu',
        ]);

        $this->assertSame(
            [
                'v1' => ['r', 's'],
                'v2' => ['c', 'u'],
            ],
            $parsed['Observation']['actionsByVersion']
        );
    }

    private function parser(): ScopePermissionParser
    {
        return new ScopePermissionParser($this->createMock(ScopeRepository::class));
    }
}
